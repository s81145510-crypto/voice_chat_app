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
                requested = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency']) for x in lines]
                recorded = [(x['account_code'], x['user_id'], x['debit'], x['credit'], x['currency']) for x in prior['lines']]
                if (existing.source, existing.currency, existing.reference, recorded) != (source, currency, reference, requested):
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

    return dict(JournalEntry=JournalEntry, JournalLine=JournalLine, serialize=serialize)
