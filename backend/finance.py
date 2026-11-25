
# -*- coding: utf-8 -*-
"""TWENTY ONE PARTY — Financial Protection Engine.
Additive module: creates its own tables only, never alters existing tables,
never moves user balances directly. All gift/commission writes are flush-only
so they stay inside the caller's transaction (atomic with the money movement).
"""
import json
from sqlalchemy import update, func, or_
from sqlalchemy.exc import IntegrityError

SCHEMES = {
    'legacy3': {'depth': 3, 'rates': [0.30, 0.15, 0.05], 'label': 'Legacy 3-level (30/15/5% of margin)'},
    'seven12': {'depth': 7, 'rates': [0.05, 0.025, 0.015, 0.01, 0.0075, 0.0075, 0.005], 'label': '7-generation (12% of pool)'},
}
DEFAULT_SCHEME = 'legacy3'
REWARD_KINDS = ('daily', 'mission', 'welcome', 'referral', 'invite', 'grant', 'streak')


def init_finance(app, db, deps):
    User = deps['User']; Tx = deps['Tx']; Purchase = deps['Purchase']; Withdraw = deps['Withdraw']
    LuckyStats = deps['LuckyStats']; PKBattle = deps['PKBattle']
    now = deps['now']; add_coins = deps['add_coins']; tx = deps['tx']
    coin_birr = deps['coin_birr']; usdt_birr = deps['usdt_birr']
    err = deps['err']; body = deps['body']; num = deps['num']
    auth = deps['auth']; admin_only = deps['admin_only']; jsonify = deps['jsonify']; request = deps['request']
    from flask import g

    class FinConfig(db.Model):
        key = db.Column(db.String(40), primary_key=True)
        value = db.Column(db.String(200), default='')
        ts = db.Column(db.Integer, default=now)

    class FinWallet(db.Model):
        user_id = db.Column(db.Integer, primary_key=True)
        kind = db.Column(db.String(16), primary_key=True)
        balance = db.Column(db.BigInteger, default=0, nullable=False)
        updated = db.Column(db.Integer, default=now)

    class FinLedger(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        user_id = db.Column(db.Integer, index=True)
        kind = db.Column(db.String(24), index=True)
        amount = db.Column(db.BigInteger, default=0)
        currency = db.Column(db.String(8), default='coins')
        ref = db.Column(db.String(64), default='')
        note = db.Column(db.String(160), default='')

    class FinGiftTx(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        sender_id = db.Column(db.Integer, index=True)
        receiver_id = db.Column(db.Integer, index=True)
        gift_key = db.Column(db.String(24))
        qty = db.Column(db.Integer, default=1)
        total = db.Column(db.Integer, default=0)
        real_used = db.Column(db.Integer, default=0)
        host_earning = db.Column(db.Integer, default=0)
        platform_share = db.Column(db.Integer, default=0)
        room_id = db.Column(db.String(16), default='')
        tier = db.Column(db.String(12), default='')
        idem = db.Column(db.String(80), unique=True)

    class FinCommission(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        agent_id = db.Column(db.Integer, index=True)
        source_user_id = db.Column(db.Integer, index=True)
        generation = db.Column(db.Integer, default=1)
        rate = db.Column(db.Float, default=0)
        pool = db.Column(db.Integer, default=0)
        amount = db.Column(db.Integer, default=0)
        gift_tx_id = db.Column(db.Integer, index=True)
        scheme = db.Column(db.String(16), default=DEFAULT_SCHEME)
        __table_args__ = (db.UniqueConstraint('gift_tx_id', 'agent_id', name='uq_fin_comm_once'),)

    class FinReferral(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        inviter_id = db.Column(db.Integer, index=True)
        invited_id = db.Column(db.Integer, unique=True)
        status = db.Column(db.String(12), default='pending')
        activity = db.Column(db.String(40), default='')
        reward = db.Column(db.Integer, default=0)
        created = db.Column(db.Integer, default=now)
        qualified = db.Column(db.Integer, nullable=True)

    class FinBudget(db.Model):
        day = db.Column(db.String(10), primary_key=True)
        kind = db.Column(db.String(16), primary_key=True)
        cap = db.Column(db.BigInteger, default=0)
        spent = db.Column(db.BigInteger, default=0, nullable=False)

    class FinAudit(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        admin_id = db.Column(db.String(32), default='')
        action = db.Column(db.String(40), index=True)
        target = db.Column(db.String(40), default='')
        details = db.Column(db.String(400), default='')

    class FinFlag(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        user_id = db.Column(db.Integer, index=True)
        kind = db.Column(db.String(32), index=True)
        severity = db.Column(db.String(8), default='warn')
        details = db.Column(db.String(300), default='')
        status = db.Column(db.String(10), default='open', index=True)

    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print('finance create_all:', e)

    def cfg_get(key, default=''):
        row = db.session.get(FinConfig, key)
        return row.value if row else default

    def cfg_set(key, value):
        row = db.session.get(FinConfig, key)
        if row: row.value = str(value); row.ts = now()
        else: db.session.add(FinConfig(key=key, value=str(value)))
        db.session.commit()

    def scheme():
        s = cfg_get('commission_scheme', DEFAULT_SCHEME)
        return s if s in SCHEMES else DEFAULT_SCHEME

    def ledger(user_id, kind, amount, currency='coins', ref='', note=''):
        db.session.add(FinLedger(user_id=user_id, kind=kind, amount=int(amount), currency=currency, ref=str(ref)[:64], note=str(note)[:160]))

    def audit(admin_id, action, target='', details=''):
        db.session.add(FinAudit(admin_id=str(admin_id), action=action, target=str(target)[:40], details=str(details)[:400]))
        try: db.session.commit()
        except Exception: db.session.rollback()

    def flag(user_id, kind, details='', severity='warn'):
        ex = FinFlag.query.filter_by(user_id=user_id, kind=kind, status='open').first()
        if ex: return ex.id
        db.session.add(FinFlag(user_id=user_id, kind=kind, details=str(details)[:300], severity=severity))
        try: db.session.commit()
        except Exception: db.session.rollback()
        return None

    def wallet_sync(user_id):
        u = db.session.get(User, user_id)
        if not u: return
        coins = int(u.coins or 0); real = int(u.real_coins or 0)
        vals = {'coins': coins, 'real': real, 'promo': max(0, coins - real),
                'earnings': int(u.earnings or 0), 'commission': int(u.commission or 0)}
        for k, v in vals.items():
            row = db.session.get(FinWallet, (user_id, k))
            if row: row.balance = v; row.updated = now()
            else: db.session.add(FinWallet(user_id=user_id, kind=k, balance=v))
        try: db.session.commit()
        except Exception: db.session.rollback()

    def record_gift(sender_id, receiver_id, gift_key, qty, total, real_used, host_earning, room_id, tier, idem):
        pool = max(0, int(real_used) - int(host_earning))
        row = FinGiftTx(sender_id=sender_id, receiver_id=receiver_id, gift_key=gift_key, qty=qty,
                        total=int(total), real_used=int(real_used), host_earning=int(host_earning),
                        platform_share=pool, room_id=str(room_id)[:16], tier=tier, idem=str(idem)[:80])
        db.session.add(row)
        db.session.flush()
        ledger(sender_id, 'gift_out', -int(total), ref=row.id, note=gift_key)
        if host_earning: ledger(receiver_id, 'host_earn', int(host_earning), ref=row.id, note=gift_key)
        ledger(0, 'platform_share', pool, ref=row.id, note=gift_key)
        return row.id, pool

    def chain_of(start_id, depth):
        out, cur, seen = [], start_id, set()
        while cur and len(out) < depth and cur not in seen:
            seen.add(cur); out.append(cur)
            a = db.session.get(User, cur)
            cur = a.parent_agent_id if a else None
        return out

    def pay_commissions(agent_id, pool, source_user_id, gift_tx_id):
        pool = int(pool or 0)
        if pool <= 0 or not agent_id or not gift_tx_id: return []
        sk = scheme(); sc = SCHEMES[sk]; paid = []; total_pct = 0.0
        for gen, aid in enumerate(chain_of(agent_id, sc['depth']), start=1):
            rate = sc['rates'][gen - 1]; total_pct += rate
            if total_pct > 1.0: break
            amount = int(pool * rate)
            if amount <= 0: continue
            if FinCommission.query.filter_by(gift_tx_id=gift_tx_id, agent_id=aid).first(): continue
            db.session.add(FinCommission(agent_id=aid, source_user_id=source_user_id, generation=gen,
                                         rate=rate, pool=pool, amount=amount, gift_tx_id=gift_tx_id, scheme=sk))
            db.session.flush()
            db.session.execute(update(User).where(User.id == aid).values(commission=User.commission + amount),
                               execution_options={'synchronize_session': False})
            tx(aid, 'commission', amount, f'G{gen} {sk}')
            ledger(aid, 'commission', amount, ref=gift_tx_id, note=f'G{gen} {sk}')
            paid.append((aid, gen, amount))
        return paid

    def referral_register(inviter_id, invited_id):
        if not inviter_id or not invited_id or inviter_id == invited_id: return
        if FinReferral.query.filter_by(invited_id=invited_id).first(): return
        db.session.add(FinReferral(inviter_id=inviter_id, invited_id=invited_id))
        db.session.flush()

    def referral_qualify(invited_id, activity, reward):
        row = FinReferral.query.filter_by(invited_id=invited_id).first()
        if not row or row.status in ('paid', 'rejected'): return 0
        if activity not in ('first_purchase', 'verified_live', 'gift_real'): return 0
        row.status = 'paid'; row.activity = activity; row.reward = int(reward); row.qualified = now()
        ledger(row.inviter_id, 'referral_reward', int(reward), ref=invited_id, note=activity)
        return int(reward)

    def reward_allowed(day, kind, amount):
        amount = int(amount or 0)
        if amount <= 0: return False
        row = db.session.get(FinBudget, (day, kind))
        if not row:
            row = db.session.get(FinBudget, (day, 'all'))
            if not row or not row.cap: return True
            return (row.spent or 0) + amount <= row.cap
        if not row.cap: return True
        return (row.spent or 0) + amount <= row.cap

    def reward_spend(day, kind, amount):
        amount = int(amount or 0)
        if amount <= 0: return
        for k in (kind, 'all'):
            row = db.session.get(FinBudget, (day, k))
            if not row:
                db.session.add(FinBudget(day=day, kind=k, cap=0, spent=0))
                try: db.session.flush()
                except IntegrityError: db.session.rollback()
            db.session.execute(update(FinBudget).where(FinBudget.day == day, FinBudget.kind == k)
                               .values(spent=FinBudget.spent + amount), execution_options={'synchronize_session': False})
        try: db.session.commit()
        except Exception: db.session.rollback()

    def pnl(days=30):
        cutoff = now() - days * 86400; rate = coin_birr()
        revenue = db.session.query(func.coalesce(func.sum(Purchase.birr), 0)).filter(Purchase.status == 'approved', Purchase.ts >= cutoff).scalar() or 0
        coins_sold = db.session.query(func.coalesce(func.sum(Purchase.coins), 0)).filter(Purchase.status == 'approved', Purchase.ts >= cutoff).scalar() or 0
        paid_out = db.session.query(func.coalesce(func.sum(Withdraw.birr), 0)).filter(Withdraw.status == 'paid', Withdraw.ts >= cutoff).scalar() or 0
        pending_out = db.session.query(func.coalesce(func.sum(Withdraw.birr), 0)).filter(Withdraw.status == 'pending').scalar() or 0
        host_liab_coins = db.session.query(func.coalesce(func.sum(User.earnings), 0)).scalar() or 0
        comm_liab_coins = db.session.query(func.coalesce(func.sum(User.commission), 0)).scalar() or 0
        promo = db.session.query(func.coalesce(func.sum(Tx.amount), 0)).filter(Tx.kind.in_(REWARD_KINDS), Tx.amount > 0, Tx.ts >= cutoff).scalar() or 0
        game_net = db.session.query(func.coalesce(func.sum(Tx.amount), 0)).filter(Tx.kind == 'game', Tx.ts >= cutoff).scalar() or 0
        lucky_house = db.session.query(func.coalesce(func.sum(LuckyStats.house), 0)).scalar() or 0
        gift_volume = db.session.query(func.coalesce(func.sum(FinGiftTx.total), 0)).filter(FinGiftTx.ts >= cutoff).scalar() or 0
        host_paid_coins = db.session.query(func.coalesce(func.sum(FinGiftTx.host_earning), 0)).filter(FinGiftTx.ts >= cutoff).scalar() or 0
        comm_paid_coins = db.session.query(func.coalesce(func.sum(FinCommission.amount), 0)).filter(FinCommission.ts >= cutoff).scalar() or 0
        host_liab_birr = round(host_liab_coins * rate, 2); comm_liab_birr = round(comm_liab_coins * rate, 2)
        est_net = round(revenue - paid_out - host_liab_birr - comm_liab_birr, 2)
        margin = round((est_net / revenue * 100), 2) if revenue else 0.0
        return dict(days=days, revenue_birr=revenue, coins_sold=coins_sold, paid_out_birr=paid_out, pending_withdraw_birr=pending_out,
                    host_liability_birr=host_liab_birr, commission_liability_birr=comm_liab_birr, promo_coins_issued=promo,
                    game_house_coins=-game_net, lucky_house_coins=lucky_house, gift_volume_coins=gift_volume,
                    host_earned_coins=host_paid_coins, commission_paid_coins=comm_paid_coins, estimated_net_birr=est_net,
                    estimated_margin_pct=margin, target_margin_pct=30, on_target=margin >= 30, scheme=scheme(), coin_birr=rate, usdt_birr=usdt_birr())

    def reconcile():
        issues = []
        bad = db.session.query(func.count()).select_from(User).filter(User.real_coins > User.coins).scalar() or 0
        if bad: issues.append(dict(kind='real_gt_coins', count=bad, severity='high'))
        neg = db.session.query(func.count()).select_from(User).filter(or_(User.coins < 0, User.earnings < 0, User.commission < 0)).scalar() or 0
        if neg: issues.append(dict(kind='negative_balance', count=neg, severity='critical'))
        dup = db.session.query(FinCommission.gift_tx_id, FinCommission.agent_id, func.count()).group_by(FinCommission.gift_tx_id, FinCommission.agent_id).having(func.count() > 1).all()
        if dup: issues.append(dict(kind='duplicate_commission', count=len(dup), severity='critical'))
        over = []
        for gid, pool, tot in db.session.query(FinGiftTx.id, FinGiftTx.platform_share, func.coalesce(func.sum(FinCommission.amount), 0)).outerjoin(
                FinCommission, FinCommission.gift_tx_id == FinGiftTx.id).group_by(FinGiftTx.id).all():
            if (tot or 0) > (pool or 0): over.append(gid)
        if over: issues.append(dict(kind='commission_exceeds_pool', count=len(over), severity='critical'))
        return dict(ok=not any(i['severity'] in ('critical', 'high') for i in issues), issues=issues,
                    users=db.session.query(func.count()).select_from(User).scalar(),
                    ledger_rows=db.session.query(func.count()).select_from(FinLedger).scalar(),
                    gift_rows=db.session.query(func.count()).select_from(FinGiftTx).scalar(),
                    commission_rows=db.session.query(func.count()).select_from(FinCommission).scalar())

    def scan():
        found = []
        for addr, n in db.session.query(Withdraw.usdt_address, func.count(func.distinct(Withdraw.user_id))).filter(
                Withdraw.usdt_address != '').group_by(Withdraw.usdt_address).having(func.count(func.distinct(Withdraw.user_id)) > 1).all():
            found.append(flag(0, 'shared_payout_address', f'{addr} used by {n} accounts', 'high'))
        for ph, n in db.session.query(Withdraw.phone, func.count(func.distinct(Withdraw.user_id))).filter(
                Withdraw.phone != '').group_by(Withdraw.phone).having(func.count(func.distinct(Withdraw.user_id)) > 2).all():
            found.append(flag(0, 'shared_payout_phone', f'{ph} used by {n} accounts', 'warn'))
        for uid, n in db.session.query(Purchase.user_id, func.count()).filter(Purchase.status == 'pending').group_by(Purchase.user_id).having(func.count() >= 3).all():
            found.append(flag(uid, 'pending_purchase_flood', f'{n} pending purchases', 'warn'))
        for a, b, n in db.session.query(FinGiftTx.sender_id, FinGiftTx.receiver_id, func.count()).group_by(
                FinGiftTx.sender_id, FinGiftTx.receiver_id).having(func.count() > 200).all():
            if FinGiftTx.query.filter_by(sender_id=b, receiver_id=a).count() > 200:
                found.append(flag(a, 'gift_cycling', f'{a}<->{b} mutual gifting', 'high'))
        return dict(flagged=len([x for x in found if x]), open_flags=FinFlag.query.filter_by(status='open').count())

    @app.get('/api/fin/pnl')
    @admin_only
    def fin_pnl():
        return jsonify(pnl(num(request.args.get('days'), 30)))

    @app.get('/api/fin/reconcile')
    @admin_only
    def fin_reconcile():
        r = reconcile(); audit(g.user.tg_id, 'reconcile', '', json.dumps(r)[:300]); return jsonify(r)

    @app.post('/api/fin/scan')
    @admin_only
    def fin_scan():
        r = scan(); audit(g.user.tg_id, 'fraud_scan', '', json.dumps(r)[:300]); return jsonify(r)

    @app.get('/api/fin/flags')
    @admin_only
    def fin_flags():
        rows = FinFlag.query.filter_by(status=request.args.get('status', 'open')).order_by(FinFlag.id.desc()).limit(100).all()
        return jsonify(items=[dict(id=f.id, ts=f.ts, user_id=f.user_id, kind=f.kind, severity=f.severity, details=f.details, status=f.status) for f in rows])

    @app.post('/api/fin/flag')
    @admin_only
    def fin_flag_resolve():
        b = body(); f = db.session.get(FinFlag, num(b.get('id')))
        if not f: return err('not found', 404)
        f.status = 'resolved' if b.get('action') == 'resolve' else 'open'
        db.session.commit(); audit(g.user.tg_id, 'flag_' + f.status, f.user_id, f.kind); return jsonify(ok=1)

    @app.get('/api/fin/wallets')
    @admin_only
    def fin_wallets():
        uid = num(request.args.get('user_id'))
        if not uid: return err('user_id required')
        wallet_sync(uid)
        rows = FinWallet.query.filter_by(user_id=uid).all()
        return jsonify(user_id=uid, wallets=[dict(kind=w.kind, balance=w.balance, updated=w.updated) for w in rows])

    @app.get('/api/fin/ledger')
    @admin_only
    def fin_ledger():
        uid = num(request.args.get('user_id')); q = FinLedger.query
        if uid: q = q.filter(FinLedger.user_id == uid)
        rows = q.order_by(FinLedger.id.desc()).limit(min(200, num(request.args.get('limit'), 60))).all()
        return jsonify(items=[dict(id=r.id, ts=r.ts, user_id=r.user_id, kind=r.kind, amount=r.amount, currency=r.currency, ref=r.ref, note=r.note) for r in rows])

    @app.get('/api/fin/gifts')
    @admin_only
    def fin_gifts():
        rows = FinGiftTx.query.order_by(FinGiftTx.id.desc()).limit(80).all()
        return jsonify(items=[dict(id=r.id, ts=r.ts, sender=r.sender_id, receiver=r.receiver_id, gift=r.gift_key, qty=r.qty,
                                   total=r.total, real=r.real_used, host=r.host_earning, pool=r.platform_share, room=r.room_id, tier=r.tier) for r in rows])

    @app.get('/api/fin/commissions')
    @admin_only
    def fin_commissions():
        aid = num(request.args.get('agent_id')); q = FinCommission.query
        if aid: q = q.filter(FinCommission.agent_id == aid)
        rows = q.order_by(FinCommission.id.desc()).limit(120).all()
        return jsonify(scheme=scheme(), items=[dict(id=r.id, ts=r.ts, agent=r.agent_id, source=r.source_user_id, gen=r.generation,
                                                    rate=r.rate, pool=r.pool, amount=r.amount, gift_tx=r.gift_tx_id, scheme=r.scheme) for r in rows])

    @app.get('/api/fin/referrals')
    @admin_only
    def fin_referrals():
        rows = FinReferral.query.order_by(FinReferral.id.desc()).limit(120).all()
        return jsonify(items=[dict(id=r.id, inviter=r.inviter_id, invited=r.invited_id, status=r.status, activity=r.activity,
                                   reward=r.reward, created=r.created, qualified=r.qualified) for r in rows])

    @app.get('/api/fin/config')
    @admin_only
    def fin_config_get():
        return jsonify(commission_scheme=scheme(), schemes={k: dict(depth=v['depth'], rates=v['rates'], label=v['label'],
                                                                     total_pct=round(sum(v['rates']) * 100, 2)) for k, v in SCHEMES.items()},
                       warning='Switching scheme changes future commissions only. Past records keep their original scheme.')

    @app.post('/api/fin/config')
    @admin_only
    def fin_config_set():
        b = body(); s = str(b.get('commission_scheme') or '')
        if s not in SCHEMES: return err('unknown scheme')
        if str(b.get('confirm') or '') != s: return err('confirm must equal the scheme name')
        old = scheme(); cfg_set('commission_scheme', s); audit(g.user.tg_id, 'commission_scheme', s, f'{old} -> {s}')
        return jsonify(ok=1, commission_scheme=s)

    @app.get('/api/fin/budgets')
    @admin_only
    def fin_budgets_get():
        rows = FinBudget.query.order_by(FinBudget.day.desc()).limit(120).all()
        return jsonify(items=[dict(day=r.day, kind=r.kind, cap=r.cap, spent=r.spent, remaining=(r.cap - r.spent) if r.cap else None) for r in rows])

    @app.post('/api/fin/budgets')
    @admin_only
    def fin_budgets_set():
        b = body(); day = str(b.get('day') or '')[:10]; kind = str(b.get('kind') or 'all')[:16]; cap = max(0, num(b.get('cap')))
        if len(day) != 10: return err('day must be YYYY-MM-DD')
        row = db.session.get(FinBudget, (day, kind))
        if row: row.cap = cap
        else: db.session.add(FinBudget(day=day, kind=kind, cap=cap, spent=0))
        db.session.commit(); audit(g.user.tg_id, 'budget_set', f'{day}/{kind}', str(cap)); return jsonify(ok=1)

    @app.get('/api/fin/audit')
    @admin_only
    def fin_audit():
        rows = FinAudit.query.order_by(FinAudit.id.desc()).limit(120).all()
        return jsonify(items=[dict(id=r.id, ts=r.ts, admin=r.admin_id, action=r.action, target=r.target, details=r.details) for r in rows])

    return dict(record_gift=record_gift, pay_commissions=pay_commissions, wallet_sync=wallet_sync,
                referral_register=referral_register, referral_qualify=referral_qualify,
                reward_allowed=reward_allowed, reward_spend=reward_spend, ledger=ledger,
                audit=audit, flag=flag, pnl=pnl, reconcile=reconcile, scan=scan, scheme=scheme)
