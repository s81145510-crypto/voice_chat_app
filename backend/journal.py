# -*- coding: utf-8 -*-
"""Append-only, balanced double-entry journal for Twenty One Party.

Additive foundation only: existing money flows are not migrated automatically.
"""
import re
from sqlalchemy import func
from flask import g


def init_journal(app, db, deps):
    now = deps['now']
    admin_only = deps['admin_only']
    jsonify = deps['jsonify']
    request = deps['request']
    body = deps['body']

    class JournalEntry(db.Model):
        __tablename__ = 'fin_journal_entry'
        id = db.Column(db.Integer, primary_key=True)
        created_at = db.Column(db.Integer, nullable=False, default=now, index=True)
        source = db.Column(db.String(40), nullable=False, index=True)
        reference = db.Column(db.String(100), nullable=False, default='')
        idempotency_key = db.Column(db.String(100), nullable=False, unique=True)
        currency = db.Column(db.String(12), nullable=False)
        description = db.Column(db.String(240), nullable=False, default='')
        created_by = db.Column(db.String(32), nullable=False, default='')
        status = db.Column(db.String(12), nullable=False, default='posted')

    class JournalLine(db.Model):
        __tablename__ = 'fin_journal_line'
        id = db.Column(db.Integer, primary_key=True)
        entry_id = db.Column(db.Integer, db.ForeignKey('fin_journal_entry.id'), nullable=False, index=True)
        line_no = db.Column(db.Integer, nullable=False)
        account_code = db.Column(db.String(48), nullable=False, index=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)
        debit = db.Column(db.BigInteger, nullable=False, default=0)
        credit = db.Column(db.BigInteger, nullable=False, default=0)
        currency = db.Column(db.String(12), nullable=False)
        memo = db.Column(db.String(160), nullable=False, default='')
        __table_args__ = (db.UniqueConstraint('entry_id', 'line_no', name='uq_fin_journal_line_no'),)

    with app.app_context():
        db.create_all()

    def serialize(entry):
        lines = JournalLine.query.filter_by(entry_id=entry.id).order_by(JournalLine.line_no).all()
        return dict(
            id=entry.id, created_at=entry.created_at, source=entry.source,
            reference=entry.reference, idempotency_key=entry.idempotency_key,
            currency=entry.currency, description=entry.description,
            created_by=entry.created_by, status=entry.status,
            total_debit=sum(int(x.debit) for x in lines),
            total_credit=sum(int(x.credit) for x in lines),
            lines=[dict(line_no=x.line_no, account_code=x.account_code,
                        user_id=x.user_id, debit=int(x.debit), credit=int(x.credit),
                        currency=x.currency, memo=x.memo) for x in lines])

    def validate_lines(lines, currency):
        if not isinstance(lines, list) or len(lines) < 2:
            raise ValueError('at least two journal lines are required')
        clean, debit_total, credit_total = [], 0, 0
        for index, line in enumerate(lines, 1):
            if not isinstance(line, dict):
                raise ValueError('each line must be an object')
            account = str(line.get('account_code') or '').strip()
            if not re.fullmatch(r'[A-Za-z0-9_.-]{2,48}', account):
                raise ValueError('invalid account_code')
            debit, credit = line.get('debit', 0), line.get('credit', 0)
            if isinstance(debit, bool) or not isinstance(debit, int):
                raise ValueError('debit must be a whole integer')
            if isinstance(credit, bool) or not isinstance(credit, int):
                raise ValueError('credit must be a whole integer')
            if debit < 0 or credit < 0 or (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
                raise ValueError('each line must have either a positive debit or a positive credit')
            line_currency = str(line.get('currency') or currency).upper()
            if line_currency != currency:
                raise ValueError('all lines in an entry must use the entry currency')
            user_id = line.get('user_id')
            if user_id is not None and (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0):
                raise ValueError('user_id must be a positive integer or null')
            clean.append(dict(line_no=index, account_code=account, user_id=user_id,
                              debit=debit, credit=credit, currency=currency,
                              memo=str(line.get('memo') or '')[:160]))
            debit_total += debit
            credit_total += credit
        if debit_total != credit_total:
            raise ValueError('unbalanced journal: total debits must equal total credits')
        return clean

    def post_internal(source, reference, idempotency_key, currency, description, lines):
        """Add a validated journal entry to the caller's transaction; never commits."""
        source = str(source or '').strip()
        idem = str(idempotency_key or '').strip()
        currency = str(currency or '').strip().upper()
        reference = str(reference or '')[:100]
        description = str(description or '')[:240]
        if not re.fullmatch(r'[A-Za-z0-9_.-]{2,40}', source):
            raise ValueError('invalid journal source')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}', idem):
            raise ValueError('invalid journal idempotency key')
        if not re.fullmatch(r'[A-Z]{3,12}', currency):
            raise ValueError('invalid journal currency')
        clean = validate_lines(lines, currency)
        existing = JournalEntry.query.filter_by(idempotency_key=idem).first()
        if existing:
            prior = serialize(existing)
            requested = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency'], x['memo']) for x in clean]
            recorded = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency'], x['memo']) for x in prior['lines']]
            if (existing.source, existing.currency, existing.reference, existing.description, recorded) != (source, currency, reference, description, requested):
                raise ValueError('journal idempotency key conflict')
            return existing, True
        entry = JournalEntry(source=source, reference=reference, idempotency_key=idem,
                             currency=currency, description=description,
                             created_by=str(getattr(getattr(g, 'user', None), 'tg_id', '') or 'system'))
        db.session.add(entry)
        db.session.flush()
        for line in clean:
            db.session.add(JournalLine(entry_id=entry.id, **line))
        db.session.flush()
        return entry, False

    @app.post('/api/fin/journal/post')
    @admin_only
    def journal_post():
        data = body()
        try:
            source = str(data.get('source') or '').strip()
            idem = str(data.get('idempotency_key') or '').strip()
            currency = str(data.get('currency') or '').strip().upper()
            if not re.fullmatch(r'[A-Za-z0-9_.-]{2,40}', source):
                raise ValueError('invalid source')
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}', idem):
                raise ValueError('idempotency_key must be 8-100 safe characters')
            if not re.fullmatch(r'[A-Z]{3,12}', currency):
                raise ValueError('currency must be a 3-12 letter uppercase code')
            lines = validate_lines(data.get('lines'), currency)
            description = str(data.get('description') or '')[:240]
            reference = str(data.get('reference') or '')[:100]
            existing = JournalEntry.query.filter_by(idempotency_key=idem).first()
            if existing:
                prior = serialize(existing)
                requested = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency'], x['memo']) for x in lines]
                recorded = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency'], x['memo']) for x in prior['lines']]
                if (existing.source, existing.currency, existing.reference, existing.description, recorded) != (source, currency, reference, description, requested):
                    return jsonify(ok=False, error='idempotency key already used with different journal data'), 409
                return jsonify(ok=True, duplicate=True, entry=prior), 200
            entry = JournalEntry(source=source, reference=reference, idempotency_key=idem,
                                 currency=currency, description=description,
                                 created_by=str(getattr(getattr(g, 'user', None), 'tg_id', '') or 'admin'))
            db.session.add(entry)
            db.session.flush()
            for line in lines:
                db.session.add(JournalLine(entry_id=entry.id, **line))
            db.session.commit()
            return jsonify(ok=True, duplicate=False, entry=serialize(entry)), 201
        except ValueError as exc:
            db.session.rollback()
            return jsonify(ok=False, error=str(exc)), 400
        except Exception:
            db.session.rollback()
            return jsonify(ok=False, error='journal posting failed; transaction rolled back'), 500

    @app.get('/api/fin/journal')
    @admin_only
    def journal_list():
        try:
            limit = max(1, min(200, int(request.args.get('limit', '50'))))
        except (TypeError, ValueError):
            limit = 50
        q = JournalEntry.query.order_by(JournalEntry.id.desc())
        if request.args.get('source'):
            q = q.filter_by(source=str(request.args.get('source'))[:40])
        if request.args.get('currency'):
            q = q.filter_by(currency=str(request.args.get('currency')).upper()[:12])
        return jsonify(items=[serialize(x) for x in q.limit(limit).all()])

    @app.get('/api/fin/journal/trial-balance')
    @admin_only
    def journal_trial_balance():
        currency = str(request.args.get('currency') or '').upper()
        if not re.fullmatch(r'[A-Z]{3,12}', currency):
            return jsonify(ok=False, error='currency query parameter is required'), 400
        rows = db.session.query(
            JournalLine.account_code,
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0)
        ).filter(JournalLine.currency == currency).group_by(JournalLine.account_code).order_by(JournalLine.account_code).all()
        accounts = [dict(account_code=code, debit=int(debit), credit=int(credit),
                          net_debit=int(debit) - int(credit)) for code, debit, credit in rows]
        debit_total = sum(x['debit'] for x in accounts)
        credit_total = sum(x['credit'] for x in accounts)
        return jsonify(currency=currency, accounts=accounts, total_debit=debit_total,
                       total_credit=credit_total, balanced=(debit_total == credit_total))

    def gift_lines(sender_id, total, real_used, host_earning, commission_total=0, lucky_refund=0, is_lucky=False):
        """Build balanced COIN journal lines for a normal or Lucky gift.

        total is the full coin spend; real_used is the purchased-coin portion.
        Promo coins are tracked separately. All inputs are whole non-negative coins.
        """
        values = (total, real_used, host_earning, commission_total, lucky_refund)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
            raise ValueError('gift amounts must be non-negative whole integers')
        if real_used > total or host_earning > real_used:
            raise ValueError('gift allocation exceeds purchased coin value')
        lines = []
        purchased = real_used
        promo = total - real_used
        if purchased:
            lines.append(dict(account_code='liability.user_coin_balance', user_id=sender_id,
                              debit=purchased, credit=0, memo='Purchased coins spent on gift'))
        if promo:
            lines.append(dict(account_code='liability.promo_coin_balance', user_id=sender_id,
                              debit=promo, credit=0, memo='Promotional coins spent on gift'))
        if is_lucky:
            # Lucky payouts are funded from the prize reserve; the gift sale and
            # the payout are shown gross so a multiplier payout remains balanced.
            lines.append(dict(account_code='revenue.lucky_gift_sales', debit=0, credit=total,
                              memo='Lucky gift coin spend'))
            lines.append(dict(account_code='expense.lucky_payout', debit=lucky_refund, credit=0,
                              memo='Lucky gift payout'))
            lines.append(dict(account_code='liability.user_coin_balance', user_id=sender_id,
                              debit=0, credit=lucky_refund, memo='Lucky payout credited to sender'))
        else:
            if host_earning:
                lines.append(dict(account_code='liability.host_earnings', debit=0, credit=host_earning,
                                  memo='Host gift earnings'))
            if commission_total:
                lines.append(dict(account_code='liability.agent_commission', debit=0, credit=commission_total,
                                  memo='Referral commissions'))
            # Commission carry can release whole coins accrued over earlier gifts.
            # Any amount above this gift's available coin spend is recognized as
            # a carry-release expense, rather than rejecting a valid gift.
            carry_release = max(0, host_earning + commission_total - total)
            if carry_release:
                lines.append(dict(account_code='expense.commission_carry_release', debit=carry_release, credit=0,
                                  memo='Previously accrued fractional commission paid'))
            retained = max(0, total - host_earning - commission_total)
            if retained:
                lines.append(dict(account_code='revenue.platform_gift', debit=0, credit=retained,
                                  memo='Platform retained gift value'))
        return lines

    def pk_gift_lines(sender_id, total, real_used):
        """Record PK gift spend; only purchased coins fund the PK prize pool."""
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (total, real_used)):
            raise ValueError('PK gift amounts must be non-negative whole integers')
        if total <= 0 or real_used > total:
            raise ValueError('invalid PK gift allocation')
        lines = []
        promo = total - real_used
        if real_used:
            lines.append(dict(account_code='liability.user_coin_balance', user_id=sender_id,
                              debit=real_used, credit=0, memo='Purchased coins committed to PK prize pool'))
            lines.append(dict(account_code='liability.pk_prize_pool', debit=0, credit=real_used,
                              memo='PK purchased-coin prize pool funding'))
        if promo:
            lines.append(dict(account_code='liability.promo_coin_balance', user_id=sender_id,
                              debit=promo, credit=0, memo='Promotional coins spent on PK gift'))
            lines.append(dict(account_code='revenue.platform_pk_gift', debit=0, credit=promo,
                              memo='Promotional PK gift spend; not prize eligible'))
        return lines

    def pk_payout_lines(real_used, payouts):
        """Settle the PK pool into host earnings and the platform's retained share."""
        if isinstance(real_used, bool) or not isinstance(real_used, int) or real_used <= 0:
            raise ValueError('PK prize pool must be a positive whole integer')
        if not isinstance(payouts, list):
            raise ValueError('PK payouts must be a list')
        lines = [dict(account_code='liability.pk_prize_pool', debit=real_used, credit=0,
                      memo='Settle PK purchased-coin prize pool')]
        paid = 0
        for payout in payouts:
            if not isinstance(payout, (tuple, list)) or len(payout) != 3:
                raise ValueError('invalid PK payout')
            user_id, amount, kind = payout
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError('invalid PK payout amount')
            # A PK may finish before a second host joins; the missing side's
            # nominal share remains with the platform rather than becoming payable.
            if user_id is None:
                continue
            if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
                raise ValueError('invalid PK payout user')
            if amount:
                lines.append(dict(account_code='liability.host_earnings', user_id=user_id,
                                  debit=0, credit=amount, memo=f'{kind} PK earnings'))
            paid += amount
        if paid > real_used:
            raise ValueError('PK payouts exceed prize pool')
        retained = real_used - paid
        if retained:
            lines.append(dict(account_code='revenue.platform_pk', debit=0, credit=retained,
                              memo='Platform retained PK pool share'))
        return lines

    def withdrawal_reserve_lines(user_id, earnings_coins, commission_coins):
        """Move withdrawable COIN balances into a pending-withdrawal liability."""
        values = (earnings_coins, commission_coins)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
            raise ValueError('withdrawal amounts must be non-negative whole integers')
        total = earnings_coins + commission_coins
        if total <= 0:
            raise ValueError('withdrawal reserve must be positive')
        lines = []
        if earnings_coins:
            lines.append(dict(account_code='liability.host_earnings', user_id=user_id,
                              debit=earnings_coins, credit=0, memo='Host earnings reserved for withdrawal'))
        if commission_coins:
            lines.append(dict(account_code='liability.agent_commission', user_id=user_id,
                              debit=commission_coins, credit=0, memo='Agent commission reserved for withdrawal'))
        lines.append(dict(account_code='liability.withdrawal_pending', user_id=user_id,
                          debit=0, credit=total, memo='Pending withdrawal payable in COIN'))
        return lines

    def withdrawal_release_lines(user_id, earnings_coins, commission_coins):
        """Reverse a rejected withdrawal and restore its original liability buckets."""
        values = (earnings_coins, commission_coins)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
            raise ValueError('withdrawal amounts must be non-negative whole integers')
        total = earnings_coins + commission_coins
        if total <= 0:
            raise ValueError('withdrawal refund must be positive')
        lines = [dict(account_code='liability.withdrawal_pending', user_id=user_id,
                      debit=total, credit=0, memo='Release rejected withdrawal reserve')]
        if earnings_coins:
            lines.append(dict(account_code='liability.host_earnings', user_id=user_id,
                              debit=0, credit=earnings_coins, memo='Restore host earnings'))
        if commission_coins:
            lines.append(dict(account_code='liability.agent_commission', user_id=user_id,
                              debit=0, credit=commission_coins, memo='Restore agent commission'))
        return lines

    return dict(JournalEntry=JournalEntry, JournalLine=JournalLine, serialize=serialize, post_internal=post_internal, gift_lines=gift_lines, pk_gift_lines=pk_gift_lines, pk_payout_lines=pk_payout_lines, withdrawal_reserve_lines=withdrawal_reserve_lines, withdrawal_release_lines=withdrawal_release_lines)
