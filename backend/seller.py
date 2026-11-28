# -*- coding: utf-8 -*-
"""Twenty One Party — Coin Seller (Reseller) Engine.
Self-contained: models + APIs + full dashboard page at /seller.
Wholesale floor 0.40 Birr/coin protects 30% margin.
"""
import os, json, secrets
from flask import Response, request, jsonify, g
from sqlalchemy import update, func, or_
from sqlalchemy.exc import IntegrityError

TIERS = [
    ('starter', 5000, 0.42, 0.05),
    ('silver', 20000, 0.41, 0.10),
    ('gold', 100000, 0.40, 0.15),
]
TIER_MAP = {t[0]: dict(key=t[0], min_birr=t[1], price=t[2], bonus=t[3]) for t in TIERS}
PRICE_FLOOR = 0.40
TRANSFER_FEE = 0.02
MIN_TRANSFER = 100
MAX_TRANSFER_DAY = 200000
RETAIL_PRICE = 0.50


def init_seller(app, db, deps):
    User = deps['User']; Tx = deps['Tx']; JOURNAL = deps.get('JOURNAL')
    now = deps['now']; tx = deps['tx']; err = deps['err']; body = deps['body']; num = deps['num']
    auth = deps['auth']; admin_only = deps['admin_only']; notify = deps['notify']
    notify_admins = deps['notify_admins']; sniff_image = deps['sniff_image']
    coin_birr = deps['coin_birr']

    class Seller(db.Model):
        user_id = db.Column(db.Integer, primary_key=True)
        status = db.Column(db.String(10), default='pending', index=True)
        tier = db.Column(db.String(10), default='starter')
        shop_name = db.Column(db.String(60), default='')
        phone = db.Column(db.String(20), default='')
        total_birr = db.Column(db.BigInteger, default=0, nullable=False)
        bought_coins = db.Column(db.BigInteger, default=0, nullable=False)
        sold_coins = db.Column(db.BigInteger, default=0, nullable=False)
        fees_paid = db.Column(db.BigInteger, default=0, nullable=False)
        joined = db.Column(db.Integer, default=now)
        approved = db.Column(db.Integer, nullable=True)

    class SellerOrder(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        seller_id = db.Column(db.Integer, index=True)
        birr = db.Column(db.Integer)
        price = db.Column(db.Float)
        coins = db.Column(db.Integer)
        bonus = db.Column(db.Integer, default=0)
        tier = db.Column(db.String(10), default='starter')
        ref = db.Column(db.String(60), unique=True)
        method = db.Column(db.String(10), default='telebirr')
        status = db.Column(db.String(10), default='pending', index=True)
        ts = db.Column(db.Integer, default=now)

    class SellerShot(db.Model):
        order_id = db.Column(db.Integer, primary_key=True)
        data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))

    class SellerTransfer(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        ts = db.Column(db.Integer, default=now, index=True)
        from_id = db.Column(db.Integer, index=True)
        to_id = db.Column(db.Integer, index=True)
        amount = db.Column(db.Integer)
        fee = db.Column(db.Integer, default=0)
        net = db.Column(db.Integer, default=0)
        request_id = db.Column(db.String(64), unique=True, nullable=True, index=True)

    with app.app_context():
        try: db.create_all()
        except Exception as e: print('seller create_all:', e)

    def tier_for(birr_total):
        best = 'starter'
        for k, mn, pr, bn in TIERS:
            if birr_total >= mn: best = k
        return best

    def seller_pub(s, u):
        t = TIER_MAP.get(s.tier, TIER_MAP['starter'])
        stock_real = max(0, int(u.real_coins or 0))
        return dict(user_id=s.user_id, name=u.username, status=s.status, tier=s.tier,
                    shop=s.shop_name or u.username, phone=s.phone or '',
                    price=t['price'], bonus=t['bonus'], min_birr=t['min_birr'],
                    total_birr=s.total_birr or 0, bought=s.bought_coins or 0,
                    sold=s.sold_coins or 0, fees=s.fees_paid or 0,
                    stock=stock_real, promo=max(0, int(u.coins or 0) - stock_real),
                    retail=RETAIL_PRICE, profit_per_coin=round(RETAIL_PRICE - t['price'], 4),
                    joined=s.joined)

    @app.post('/api/seller/apply')
    @auth
    def seller_apply():
        b = body(); u = g.user
        s = db.session.get(Seller, u.id)
        if s and s.status == 'approved': return err('Already a seller')
        shop = str(b.get('shop') or '').strip()[:60]
        phone = str(b.get('phone') or u.phone or '').strip()
        if len(shop) < 2: return err('Enter shop name')
        if not (phone.isdigit() and len(phone) == 10): return err('Enter valid phone 09xxxxxxxx')
        if s:
            s.status = 'pending'; s.shop_name = shop; s.phone = phone
        else:
            db.session.add(Seller(user_id=u.id, status='pending', shop_name=shop, phone=phone))
        db.session.commit()
        notify_admins(f'🏪 Seller application: {u.username} ({shop}) {phone}')
        return jsonify(ok=1)

    @app.get('/api/seller/me')
    @auth
    def seller_me():
        u = g.user; s = db.session.get(Seller, u.id)
        if not s: return jsonify(seller=None, tiers=[TIER_MAP[t[0]] for t in TIERS], floor=PRICE_FLOOR)
        orders = SellerOrder.query.filter_by(seller_id=u.id).order_by(SellerOrder.id.desc()).limit(20).all()
        trs = SellerTransfer.query.filter_by(from_id=u.id).order_by(SellerTransfer.id.desc()).limit(20).all()
        names = {x.id: x.username for x in User.query.filter(User.id.in_([t.to_id for t in trs] or [0])).all()}
        day_start = now() - 86400
        used = db.session.query(func.coalesce(func.sum(SellerTransfer.amount), 0)).filter(
            SellerTransfer.from_id == u.id, SellerTransfer.ts >= day_start).scalar() or 0
        return jsonify(seller=seller_pub(s, u), tiers=[TIER_MAP[t[0]] for t in TIERS], floor=PRICE_FLOOR,
                       fee=TRANSFER_FEE, min_transfer=MIN_TRANSFER, day_left=max(0, MAX_TRANSFER_DAY - used),
                       orders=[dict(id=o.id, birr=o.birr, coins=o.coins, bonus=o.bonus, price=o.price,
                                    tier=o.tier, status=o.status, ts=o.ts, ref=o.ref) for o in orders],
                       transfers=[dict(id=t.id, to=t.to_id, name=names.get(t.to_id, '?'), amount=t.amount,
                                       fee=t.fee, net=t.net, ts=t.ts) for t in trs])

    @app.post('/api/seller/order')
    @auth
    def seller_order():
        u = g.user; s = db.session.get(Seller, u.id)
        if not s or s.status != 'approved': return err('Not an approved seller', 403)
        birr = num(request.form.get('birr'))
        ref = (request.form.get('ref') or '').strip().upper()[:60]
        method = (request.form.get('method') or 'telebirr').strip()
        if method not in ('telebirr', 'usdt'): method = 'telebirr'
        t = TIER_MAP[s.tier]
        if birr < t['min_birr']: return err(f"Minimum for {s.tier}: {t['min_birr']} Birr")
        if birr > 5000000: return err('Amount too large')
        if len(ref) < 6: return err('Enter payment reference / TX hash')
        f = request.files.get('file'); data = f.read() if f else b''; mime = sniff_image(data)
        if not mime: return err('Attach payment screenshot')
        if SellerOrder.query.filter_by(seller_id=u.id, status='pending').count() >= 3:
            return err('3 pending orders already')
        price = max(PRICE_FLOOR, t['price'])
        coins = int(birr / price); bonus = int(coins * t['bonus'])
        o = SellerOrder(seller_id=u.id, birr=birr, price=price, coins=coins, bonus=bonus,
                        tier=s.tier, ref=ref, method=method)
        db.session.add(o)
        try:
            db.session.flush(); db.session.add(SellerShot(order_id=o.id, data=data, mime=mime)); db.session.commit()
        except IntegrityError:
            db.session.rollback(); return err('Reference already used')
        notify_admins(f'🏪 WHOLESALE: {u.username} [{s.tier}] {birr} Birr → {coins} coins +{bonus} promo\nRef: {ref}')
        return jsonify(ok=1, coins=coins, bonus=bonus, price=price)

    @app.get('/api/seller/lookup')
    @auth
    def seller_lookup():
        s = db.session.get(Seller, g.user.id)
        if not s or s.status != 'approved': return err('Not a seller', 403)
        q = (request.args.get('q') or '').strip()
        if len(q) < 2: return err('Enter user id or name')
        qs = User.query.filter(User.banned == False, User.id != g.user.id)
        if q.isdigit(): qs = qs.filter(or_(User.id == int(q), User.tg_id == q))
        else: qs = qs.filter(User.username.ilike(f'%{q}%'))
        rows = qs.limit(10).all()
        return jsonify(users=[dict(id=x.id, name=x.username, tg=x.tg_id) for x in rows])

    @app.post('/api/seller/transfer')
    @auth
    def seller_transfer():
        u = g.user; s = db.session.get(Seller, u.id)
        if not s or s.status != 'approved': return err('Not an approved seller', 403)
        b = body(); to = num(b.get('to')); amt = num(b.get('amount'))
        request_id = str(b.get('request_id') or '').strip()
        if not (16 <= len(request_id) <= 64 and all(ch.isalnum() or ch in '_-' for ch in request_id)):
            return err('A valid transfer request ID is required')
        prior = SellerTransfer.query.filter_by(request_id=request_id).first()
        if prior:
            if (prior.from_id, prior.to_id, prior.amount) != (u.id, to, amt):
                return err('Request ID was already used for a different transfer', 409)
            return jsonify(ok=1, sent=prior.amount, fee=prior.fee, received=prior.net,
                           transfer_id=prior.id, replayed=True)
        if amt < MIN_TRANSFER: return err(f'Minimum transfer {MIN_TRANSFER} coins')
        if to == u.id: return err('Cannot transfer to yourself')
        target = db.session.get(User, to)
        if not target: return err('Buyer not found')
        if target.banned: return err('Buyer is banned')
        day_start = now() - 86400
        used = db.session.query(func.coalesce(func.sum(SellerTransfer.amount), 0)).filter(
            SellerTransfer.from_id == u.id, SellerTransfer.ts >= day_start).scalar() or 0
        if used + amt > MAX_TRANSFER_DAY: return err(f'Daily limit {MAX_TRANSFER_DAY} coins reached')
        fee = int(amt * TRANSFER_FEE); net = amt - fee
        if net <= 0: return err('Amount too small')
        r = db.session.execute(update(User).where(User.id == u.id, User.coins >= amt, User.real_coins >= amt)
                               .values(coins=User.coins - amt, real_coins=User.real_coins - amt),
                               execution_options={'synchronize_session': False})
        if r.rowcount != 1:
            db.session.rollback(); return err('Not enough TRADE coins (promo coins cannot be transferred)', 402)
        db.session.execute(update(User).where(User.id == to).values(coins=User.coins + net, real_coins=User.real_coins + net),
                           execution_options={'synchronize_session': False})
        transfer = SellerTransfer(from_id=u.id, to_id=to, amount=amt, fee=fee, net=net, request_id=request_id)
        db.session.add(transfer)
        try:
            db.session.flush()
            if JOURNAL:
                JOURNAL['post_internal'](
                    source='seller_transfer', reference=str(transfer.id),
                    idempotency_key=f'seller-transfer:{transfer.id}:COIN',
                    currency='COIN',
                    description=f'Seller {u.id} transferred {amt} purchased coins to buyer {to}',
                    lines=JOURNAL['seller_transfer_lines'](int(u.id), int(to), int(amt), int(fee), int(net)))
            db.session.execute(update(Seller).where(Seller.user_id == u.id)
                               .values(sold_coins=Seller.sold_coins + amt, fees_paid=Seller.fees_paid + fee),
                               execution_options={'synchronize_session': False})
            tx(u.id, 'sell', -amt, f'to {to} fee {fee}')
            tx(to, 'buy_from_seller', net, f'from {u.id}')
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            prior = SellerTransfer.query.filter_by(request_id=request_id).first()
            if prior and (prior.from_id, prior.to_id, prior.amount) == (u.id, to, amt):
                return jsonify(ok=1, sent=prior.amount, fee=prior.fee, received=prior.net,
                               transfer_id=prior.id, replayed=True)
            return err('Transfer request ID conflict; refresh and verify transfer history', 409)
        except Exception:
            db.session.rollback()
            return err('Seller transfer accounting failed; no transfer was applied', 500)
        notify(target.tg_id, f'🪙 You received {net} coins from {s.shop_name or u.username}!')
        return jsonify(ok=1, sent=amt, fee=fee, received=net, transfer_id=transfer.id, replayed=False)

    @app.get('/api/seller/admin/list')
    @admin_only
    def seller_admin_list():
        st = request.args.get('status', '')
        q = Seller.query
        if st: q = q.filter_by(status=st)
        rows = q.order_by(Seller.joined.desc()).limit(100).all()
        us = {x.id: x for x in User.query.filter(User.id.in_([r.user_id for r in rows] or [0])).all()}
        return jsonify(items=[seller_pub(r, us[r.user_id]) for r in rows if r.user_id in us])

    @app.post('/api/seller/admin/seller')
    @admin_only
    def seller_admin_seller():
        b = body(); uid = num(b.get('id')); act = b.get('action')
        s = db.session.get(Seller, uid)
        if not s: return err('not found', 404)
        u = db.session.get(User, uid)
        if act == 'approve':
            s.status = 'approved'; s.approved = now(); s.tier = tier_for(s.total_birr or 0)
        elif act == 'reject': s.status = 'rejected'
        elif act == 'suspend': s.status = 'suspended'
        elif act == 'tier':
            t = str(b.get('tier') or '')
            if t not in TIER_MAP: return err('bad tier')
            s.tier = t
        else: return err('bad action')
        db.session.commit()
        if u: notify(u.tg_id, f'🏪 Seller status: {s.status} ({s.tier})')
        return jsonify(ok=1)

    @app.get('/api/seller/admin/orders')
    @admin_only
    def seller_admin_orders():
        rows = SellerOrder.query.filter_by(status=request.args.get('status', 'pending')).order_by(SellerOrder.id.desc()).limit(60).all()
        us = {x.id: x.username for x in User.query.filter(User.id.in_([r.seller_id for r in rows] or [0])).all()}
        return jsonify(items=[dict(id=o.id, seller=o.seller_id, name=us.get(o.seller_id, '?'), birr=o.birr,
                                   coins=o.coins, bonus=o.bonus, price=o.price, tier=o.tier, ref=o.ref,
                                   method=o.method, ts=o.ts) for o in rows])

    @app.get('/api/seller/admin/shot/<int:oid>')
    @admin_only
    def seller_admin_shot(oid):
        s = db.session.get(SellerShot, oid)
        if not s: return Response(status=404)
        return Response(s.data, mimetype=s.mime)

    @app.post('/api/seller/admin/order')
    @admin_only
    def seller_admin_order():
        b = body(); oid = num(b.get('id')); act = b.get('action')
        o = db.session.get(SellerOrder, oid)
        if not o: return err('not found', 404)
        new = 'approved' if act == 'approve' else 'rejected'
        r = db.session.execute(update(SellerOrder).where(SellerOrder.id == oid, SellerOrder.status == 'pending')
                               .values(status=new), execution_options={'synchronize_session': False})
        if r.rowcount != 1:
            db.session.rollback(); return err('Already processed')
        u = db.session.get(User, o.seller_id); s = db.session.get(Seller, o.seller_id)
        if act == 'approve':
            total = o.coins + o.bonus
            db.session.execute(update(User).where(User.id == o.seller_id)
                               .values(coins=User.coins + total, real_coins=User.real_coins + o.coins),
                               execution_options={'synchronize_session': False})
            tx(o.seller_id, 'wholesale', o.coins, f'order #{o.id} {o.birr} Birr')
            if o.bonus: tx(o.seller_id, 'wholesale_bonus', o.bonus, f'order #{o.id} promo')
            if s:
                s.total_birr = (s.total_birr or 0) + o.birr
                s.bought_coins = (s.bought_coins or 0) + o.coins
                s.tier = tier_for(s.total_birr)
        db.session.commit()
        if u: notify(u.tg_id, f'✅ {o.coins} trade coins +{o.bonus} promo added!' if act == 'approve' else '❌ Wholesale order rejected.')
        return jsonify(ok=1)

    @app.get('/api/seller/admin/stats')
    @admin_only
    def seller_admin_stats():
        return jsonify(sellers=Seller.query.filter_by(status='approved').count(),
                       pending=Seller.query.filter_by(status='pending').count(),
                       orders_pending=SellerOrder.query.filter_by(status='pending').count(),
                       revenue=db.session.query(func.coalesce(func.sum(SellerOrder.birr), 0)).filter(SellerOrder.status == 'approved').scalar() or 0,
                       coins_issued=db.session.query(func.coalesce(func.sum(SellerOrder.coins), 0)).filter(SellerOrder.status == 'approved').scalar() or 0,
                       bonus_issued=db.session.query(func.coalesce(func.sum(SellerOrder.bonus), 0)).filter(SellerOrder.status == 'approved').scalar() or 0,
                       transferred=db.session.query(func.coalesce(func.sum(SellerTransfer.amount), 0)).scalar() or 0,
                       fees=db.session.query(func.coalesce(func.sum(SellerTransfer.fee), 0)).scalar() or 0)

    @app.get('/seller')
    def seller_page():
        return Response(SELLER_HTML, mimetype='text/html', headers={'Cache-Control': 'no-cache'})

    return dict(tier_for=tier_for, TIERS=TIERS, TIER_MAP=TIER_MAP)


SELLER_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0118"><title>Coin Seller — Twenty One</title>
<style>
:root{--bg:#0a0118;--card:rgba(255,255,255,.07);--line:rgba(255,255,255,.12);--gold:#ffd700;--pri:#667eea;--pri2:#9b5cff;--ok:#2ee59d;--bad:#ff5c7a;--mut:#a99bc4}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:linear-gradient(160deg,#0a0118,#1a0a2e) fixed;color:#fff;font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:14px 14px 40px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;color:var(--mut);margin:18px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center}.sp{flex:1;min-width:0}
.mut{color:var(--mut);font-size:12px}.gold{color:var(--gold)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{padding:11px 14px;border-radius:12px;border:0;background:linear-gradient(135deg,var(--pri),var(--pri2));color:#fff;font-weight:700;cursor:pointer}
.btn.gold{background:linear-gradient(135deg,#ffd700,#ff9f1c);color:#2a1a00}
.btn.ghost{background:var(--card);border:1px solid var(--line);color:#fff}
.btn.sm{padding:7px 11px;font-size:13px}.btn[disabled]{opacity:.5}
input,select{width:100%;padding:11px;border-radius:12px;background:rgba(255,255,255,.08);border:1px solid var(--line);color:#fff;outline:0;margin-bottom:8px}
.chips{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;margin-bottom:10px}
.chip{padding:7px 13px;border-radius:20px;background:var(--card);border:1px solid var(--line);font-size:13px;white-space:nowrap;cursor:pointer}
.chip.on{background:linear-gradient(135deg,var(--pri),var(--pri2));border-color:transparent}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left}
.badge{font-size:11px;padding:2px 7px;border-radius:8px;background:rgba(155,92,255,.25)}
.toast{position:fixed;left:50%;top:14px;transform:translateX(-50%);background:#000d;padding:10px 16px;border-radius:12px;z-index:99;border:1px solid var(--line)}
.empty{text-align:center;color:var(--mut);padding:22px}
.big{font-size:24px;font-weight:800}
a{color:var(--pri2)}
</style></head><body>
<div class="row"><div class="sp"><h1>🏪 Coin Seller</h1><div class="mut">Wholesale · Resell · Profit</div></div><a href="/" class="btn ghost sm">← App</a></div>
<div id="root"><div class="empty">Loading…</div></div>
<script>
let TK=localStorage.getItem('tk')||'',TAB='dash',CACHE=null;
const $=s=>document.querySelector(s);
const fmt=n=>Number(n||0).toLocaleString('en-US');
const f2=n=>Number(n||0).toFixed(2);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(m){const e=document.createElement('div');e.className='toast';e.textContent=m;document.body.appendChild(e);setTimeout(()=>e.remove(),2500)}
async function api(p,b){const o={headers:{}};if(TK)o.headers.Authorization='Bearer '+TK;
 if(b!==undefined){o.method='POST';if(b instanceof FormData)o.body=b;else{o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}}
 const r=await fetch(p,o);const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'Error');return d}
async function run(btn,fn){if(btn)btn.disabled=true;try{return await fn()}catch(e){toast(e.message)}finally{if(btn)btn.disabled=false}}
async function shrink(file,max=1100,q=.8){return new Promise((res,rej)=>{const i=new Image();i.onload=()=>{const k=Math.min(1,max/Math.max(i.width,i.height));const c=document.createElement('canvas');c.width=i.width*k;c.height=i.height*k;c.getContext('2d').drawImage(i,0,0,c.width,c.height);c.toBlob(b=>b?res(b):rej(new Error('image')),'image/jpeg',q)};i.onerror=()=>rej(new Error('Not an image'));i.src=URL.createObjectURL(file)})}
function tierTable(tiers,cur){return '<table><tr><th>Tier</th><th>Min Birr</th><th>Price</th><th>Bonus</th></tr>'+
 tiers.map(t=>'<tr'+(t.key===cur?' style="color:#ffd700"':'')+'><td>'+t.key+'</td><td>'+fmt(t.min_birr)+'</td><td>'+f2(t.price)+'</td><td>+'+Math.round(t.bonus*100)+'%</td></tr>').join('')+'</table>'}
function applyView(tiers){return '<div class="card"><h2 style="margin-top:0">Become a Coin Seller</h2>'+
 '<div class="mut" style="margin-bottom:10px">Buy coins at wholesale, resell at 0.50 Birr/coin, keep the profit.</div>'+
 tierTable(tiers,'')+'<div class="mut" style="margin:10px 0">Bonus coins are promo (own gifting only, not transferable).</div>'+
 '<input id="a-shop" placeholder="Shop name" maxlength="60"><input id="a-phone" placeholder="Phone 09xxxxxxxx" maxlength="10">'+
 '<button class="btn gold" style="width:100%" id="a-go">Apply now</button></div>'}
function dashView(d){const s=d.seller;
 let h='<div class="grid"><div class="card"><div class="mut">Trade stock</div><div class="big gold">'+fmt(s.stock)+'</div><div class="mut">transferable</div></div>'+
 '<div class="card"><div class="mut">Promo</div><div class="big">'+fmt(s.promo)+'</div><div class="mut">own use only</div></div>'+
 '<div class="card"><div class="mut">My price</div><div class="big ok">'+f2(s.price)+'</div><div class="mut">Birr / coin</div></div>'+
 '<div class="card"><div class="mut">Profit / coin</div><div class="big gold">'+f2(s.profit_per_coin)+'</div><div class="mut">sell at '+f2(s.retail)+'</div></div></div>'+
 '<div class="card"><div class="row"><b class="sp">'+esc(s.shop)+'</b><span class="badge">'+s.tier.toUpperCase()+'</span></div>'+
 '<div class="mut">Total bought: '+fmt(s.total_birr)+' Birr · Sold: '+fmt(s.sold)+' coins · Fees: '+fmt(s.fees)+'</div></div>'+
 '<div class="chips"><div class="chip'+(TAB==='dash'?' on':'')+'" data-t="dash">Overview</div><div class="chip'+(TAB==='buy'?' on':'')+'" data-t="buy">Buy wholesale</div><div class="chip'+(TAB==='sell'?' on':'')+'" data-t="sell">Sell / Transfer</div><div class="chip'+(TAB==='hist'?' on':'')+'" data-t="hist">History</div></div>';
 if(TAB==='buy'){h+='<div class="card"><h2 style="margin-top:0">Wholesale order</h2>'+tierTable(d.tiers,s.tier)+
  '<div class="mut" style="margin:10px 0">Min for your tier: <b class="gold">'+fmt(s.min_birr)+' Birr</b></div>'+
  '<input id="o-birr" type="number" inputmode="numeric" placeholder="Amount in Birr (min '+s.min_birr+')">'+
  '<select id="o-method"><option value="telebirr">Telebirr</option><option value="usdt">USDT (TRC20)</option></select>'+
  '<input id="o-ref" placeholder="Transaction ID / TX hash"><input id="o-file" type="file" accept="image/*">'+
  '<div id="o-calc" class="mut" style="margin-bottom:8px"></div>'+
  '<button class="btn gold" style="width:100%" id="o-go">Submit order</button></div>'}
 else if(TAB==='sell'){h+='<div class="card"><h2 style="margin-top:0">Transfer to buyer</h2>'+
  '<div class="mut" style="margin-bottom:8px">Fee '+Math.round(d.fee*100)+'% · min '+fmt(d.min_transfer)+' · daily left '+fmt(d.day_left)+'</div>'+
  '<input id="t-q" placeholder="Buyer user ID or name"><button class="btn ghost sm" id="t-find" style="margin-bottom:8px">Find buyer</button>'+
  '<div id="t-list"></div><input id="t-to" type="number" inputmode="numeric" placeholder="Buyer ID">'+
  '<input id="t-amt" type="number" inputmode="numeric" placeholder="Coins to send">'+
  '<div id="t-calc" class="mut" style="margin-bottom:8px"></div>'+
  '<button class="btn gold" style="width:100%" id="t-go">Send coins</button></div>'}
 else if(TAB==='hist'){h+='<div class="card"><h2 style="margin-top:0">Orders</h2>'+
  (d.orders.length?'<table><tr><th>Birr</th><th>Coins</th><th>Status</th></tr>'+d.orders.map(o=>'<tr><td>'+fmt(o.birr)+'</td><td>'+fmt(o.coins)+' +'+fmt(o.bonus)+'</td><td>'+o.status+'</td></tr>').join('')+'</table>':'<div class="empty">No orders</div>')+'</div>'+
  '<div class="card"><h2 style="margin-top:0">Sales</h2>'+
  (d.transfers.length?'<table><tr><th>Buyer</th><th>Sent</th><th>Fee</th></tr>'+d.transfers.map(t=>'<tr><td>'+esc(t.name)+'</td><td>'+fmt(t.amount)+'</td><td>'+fmt(t.fee)+'</td></tr>').join('')+'</table>':'<div class="empty">No sales</div>')+'</div>'}
 else{h+='<div class="card"><h2 style="margin-top:0">How it works</h2><div class="mut" style="line-height:1.9">'+
  '1️⃣ Submit wholesale order + payment proof<br>2️⃣ Admin approves → trade coins arrive<br>3️⃣ Transfer coins to your buyers<br>4️⃣ Buyer pays you '+f2(s.retail)+' Birr/coin<br>5️⃣ You keep '+f2(s.profit_per_coin)+' Birr per coin<br><br>'+
  '⚠️ Promo bonus coins cannot be transferred.<br>⚠️ Daily transfer limit applies.<br>⚠️ Higher total purchase = better tier.</div></div>'+
  '<div class="card"><h2 style="margin-top:0">All tiers</h2>'+tierTable(d.tiers,s.tier)+'</div>'}
 return h}
function bind(d){
 document.querySelectorAll('[data-t]').forEach(c=>c.onclick=()=>{TAB=c.dataset.t;render()});
 const ab=$('#a-go');if(ab)ab.onclick=e=>run(e.target,async()=>{await api('/api/seller/apply',{shop:$('#a-shop').value,phone:$('#a-phone').value});toast('✅ Application sent');load()});
 const ob=$('#o-birr');if(ob)ob.oninput=()=>{const v=+ob.value||0;const s=d.seller;const c=Math.floor(v/s.price);$('#o-calc').innerHTML='You get <b class="gold">'+fmt(c)+'</b> trade + <b>'+fmt(Math.floor(c*s.bonus))+'</b> promo coins'};
 const og=$('#o-go');if(og)og.onclick=e=>run(e.target,async()=>{const f=$('#o-file').files[0];if(!f)throw new Error('Attach screenshot');
  const fd=new FormData();fd.append('birr',$('#o-birr').value);fd.append('ref',$('#o-ref').value);fd.append('method',$('#o-method').value);fd.append('file',await shrink(f),'p.jpg');
  const r=await api('/api/seller/order',fd);toast('✅ Order sent: '+fmt(r.coins)+' coins');TAB='hist';load()});
 const tf=$('#t-find');if(tf)tf.onclick=e=>run(e.target,async()=>{const r=await api('/api/seller/lookup?q='+encodeURIComponent($('#t-q').value));
  $('#t-list').innerHTML=r.users.length?r.users.map(u=>'<div class="row" style="padding:6px 0"><div class="sp">'+esc(u.name)+' <span class="mut">#'+u.id+'</span></div><button class="btn sm ghost" data-pick="'+u.id+'">Pick</button></div>').join(''):'<div class="mut">Not found</div>';
  document.querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{$('#t-to').value=b.dataset.pick;toast('Buyer selected')})});
 const ta=$('#t-amt');if(ta)ta.oninput=()=>{const v=+ta.value||0;const fee=Math.floor(v*d.fee);$('#t-calc').innerHTML='Buyer receives <b class="gold">'+fmt(v-fee)+'</b> (fee '+fmt(fee)+')'};
 const tg=$('#t-go');if(tg)tg.onclick=e=>run(e.target,async()=>{const to=+$('#t-to').value,amount=+$('#t-amt').value;
  if(!confirm('Send '+amount+' coins?'))return;
  const storageKey='seller-transfer-pending:'+CACHE.seller.user_id;
  let pending=null;try{pending=JSON.parse(localStorage.getItem(storageKey)||'null')}catch(_){}
  if(pending&&(pending.to!==to||pending.amount!==amount))throw new Error('A previous transfer may still be processing. Retry the same buyer and amount first.');
  if(!pending){const requestId=(typeof crypto!=='undefined'&&crypto.randomUUID)?crypto.randomUUID().replace(/-/g,''):Array.from({length:32},()=>Math.floor(Math.random()*16).toString(16)).join('');
   pending={request_id:requestId,to,amount};localStorage.setItem(storageKey,JSON.stringify(pending))}
  const r=await api('/api/seller/transfer',pending);
  localStorage.removeItem(storageKey);toast((r.replayed?'↻ Already processed. ':'✅ Sent! ')+'Buyer got '+fmt(r.received));TAB='hist';load()})}
function render(){const d=CACHE;const root=$('#root');
 if(!d.seller){root.innerHTML=applyView(d.tiers)}
 else if(d.seller.status!=='approved'){root.innerHTML='<div class="card"><h2 style="margin-top:0">Status: '+d.seller.status+'</h2><div class="mut">Your application is '+d.seller.status+'. You will be notified by Telegram.</div></div>'+applyView(d.tiers)}
 else{root.innerHTML=dashView(d)}
 bind(d)}
async function load(){CACHE=await api('/api/seller/me');render()}
(async()=>{try{if(!TK){$('#root').innerHTML='<div class="empty">Open the main app first, then come back here.<br><br><a href="/" class="btn gold sm">Open app</a></div>';return}await load()}catch(e){$('#root').innerHTML='<div class="empty bad">'+esc(e.message)+'</div>'}})();
</script></body></html>"""
