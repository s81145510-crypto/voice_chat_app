# -*- coding: utf-8 -*-
"""Phone number + password login for Twenty One Party."""
import re, secrets, hashlib
from flask import request, jsonify, g
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

MIN_PASS, MAX_PASS = 6, 72
WINDOW = 15 * 60
PHONE_FAILS = 6        # wrong passwords per phone per window
IP_FAILS = 60          # generous: many mobile users share one IP
IP_SIGNUPS = 15        # new accounts per IP per hour
WEAK = {'123456', '1234567', '12345678', '123456789', '000000', '111111', 'password', 'qwerty', 'abc123'}


def normalize_phone(raw):
    """09xxxxxxxx / 07xxxxxxxx / +2519... / 2517...  ->  09xxxxxxxx, else None."""
    p = re.sub(r'[\s\-()]', '', str(raw or ''))
    if p.startswith('+251'): p = '0' + p[4:]
    elif p.startswith('251') and len(p) == 12: p = '0' + p[3:]
    return p if re.fullmatch(r'0[79][0-9]{8}', p) else None


def client_ip():
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or '?')[:45]


def init_auth(app, db, d):
    User, now, tx, err, body, num = d['User'], d['now'], d['tx'], d['err'], d['body'], d['num']
    auth, admin_only, sign, me_dict, cfg = d['auth'], d['admin_only'], d['sign'], d['me_dict'], d['cfg']
    welcome, referral_register, is_admin = d['welcome'], d['referral_register'], d['is_admin']

    class Credential(db.Model):
        user_id = db.Column(db.Integer, primary_key=True)
        phone = db.Column(db.String(10), unique=True, nullable=False, index=True)
        pw_hash = db.Column(db.String(256), nullable=False)
        created = db.Column(db.Integer, default=now)

    class AuthThrottle(db.Model):
        # stored in the database so the limit is shared by all gunicorn workers
        key = db.Column(db.String(64), primary_key=True)
        count = db.Column(db.Integer, default=0, nullable=False)
        start = db.Column(db.Integer, default=now, index=True)

    class AppSecret(db.Model):
        key = db.Column(db.String(20), primary_key=True)
        value = db.Column(db.String(128), nullable=False)

    with app.app_context():
        try: db.create_all()
        except Exception as e: print('auth create_all:', e)

    def stored_secret():
        """Random session key kept in the database (used when SECRET_KEY / BOT_TOKEN are not set)."""
        with app.app_context():
            row = db.session.get(AppSecret, 'session')
            if not row:
                try:
                    db.session.add(AppSecret(key='session', value=secrets.token_hex(32))); db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                row = db.session.get(AppSecret, 'session')
            return hashlib.sha256(('sess:' + row.value).encode()).digest()

    def hkey(kind, value):
        return kind + ':' + hashlib.sha256(value.encode()).hexdigest()[:40]

    def hit(key, limit, window):
        """Count one attempt first (atomic), then say whether it is still allowed."""
        t = now()
        for _ in range(2):
            db.session.execute(update(AuthThrottle).where(AuthThrottle.key == key, AuthThrottle.start <= t - window).values(count=0, start=t),
                               execution_options={'synchronize_session': False})
            r = db.session.execute(update(AuthThrottle).where(AuthThrottle.key == key).values(count=AuthThrottle.count + 1),
                                   execution_options={'synchronize_session': False})
            if r.rowcount:
                n = db.session.query(AuthThrottle.count).filter(AuthThrottle.key == key).scalar()
                db.session.commit(); return n <= limit
            try:
                db.session.add(AuthThrottle(key=key, count=1, start=t)); db.session.commit(); return True
            except IntegrityError:
                db.session.rollback()
        return False

    def give_back(key):
        db.session.execute(update(AuthThrottle).where(AuthThrottle.key == key, AuthThrottle.count > 0).values(count=AuthThrottle.count - 1),
                           execution_options={'synchronize_session': False})

    def clear(key):
        AuthThrottle.query.filter(AuthThrottle.key == key).delete(synchronize_session=False)

    def password_problem(pw):
        if not isinstance(pw, str) or len(pw) < MIN_PASS: return f'Password must be at least {MIN_PASS} characters'
        if len(pw) > MAX_PASS: return 'Password is too long'
        if pw.lower() in WEAK: return 'This password is too easy. Choose another one'
        return None

    def session_reply(u):
        return jsonify(token=sign(u.id), me=me_dict(u), cfg=cfg())

    dummy_hash = generate_password_hash(secrets.token_hex(16))

    @app.post('/api/auth/signup')
    def auth_signup():
        b = body(); phone = normalize_phone(b.get('phone')); pw = b.get('password')
        name = str(b.get('name') or '').strip()[:30]
        if not phone: return err('Enter a valid phone number (09xxxxxxxx)')
        if len(name) < 2: return err('Enter your name')
        problem = password_problem(pw)
        if problem: return err(problem)
        if Credential.query.filter_by(phone=phone).first(): return err('This number already has an account. Please log in', 409)
        if not hit(hkey('su', client_ip()), IP_SIGNUPS, 3600): return err('Too many new accounts. Try again in 1 hour', 429)
        start = str(b.get('ref') or '')[:40]; ref = ag = None
        if start.startswith('ref_'):
            r = User.query.filter_by(tg_id=start[4:]).first(); ref = r.id if r else None
        elif start.startswith('ag_'):
            a = User.query.filter_by(tg_id=start[3:]).first(); ag = a.id if a and a.is_agent else None
        # random public id: never exposes the phone number and cannot be guessed
        u = User(tg_id='p_' + secrets.token_hex(8), username=name, phone=phone, coins=welcome, referrer_id=ref, agent_id=ag)
        try:
            db.session.add(u); db.session.flush()
            db.session.add(Credential(user_id=u.id, phone=phone, pw_hash=generate_password_hash(pw)))
            tx(u.id, 'welcome', welcome, 'Welcome bonus')
            if ref: referral_register(ref, u.id)
            if ag: referral_register(ag, u.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); return err('This number already has an account. Please log in', 409)
        return session_reply(u)

    @app.post('/api/auth/login')
    def auth_login():
        b = body(); phone = normalize_phone(b.get('phone')); pw = b.get('password')
        if not phone or not isinstance(pw, str) or not 0 < len(pw) <= MAX_PASS: return err('Enter phone number and password')
        pk, ik = hkey('ph', phone), hkey('ip', client_ip())
        if not hit(ik, IP_FAILS, WINDOW) or not hit(pk, PHONE_FAILS, WINDOW):
            return err('Too many attempts. Try again in 15 minutes', 429)
        c = Credential.query.filter_by(phone=phone).first()
        ok = check_password_hash(c.pw_hash if c else dummy_hash, pw)
        u = db.session.get(User, c.user_id) if c and ok else None
        if not u: return err('Wrong phone number or password', 401)
        if u.banned: return err('Your account is banned', 403)
        clear(pk); give_back(ik); db.session.commit()
        return session_reply(u)

    @app.post('/api/auth/password')
    @auth
    def auth_password():
        b = body(); c = db.session.get(Credential, g.user.id)
        if not c: return err('This account does not use a password')
        key = hkey('pw', str(g.user.id))
        if not hit(key, PHONE_FAILS, WINDOW): return err('Too many attempts. Try again in 15 minutes', 429)
        old = b.get('old')
        if not isinstance(old, str) or len(old) > MAX_PASS or not check_password_hash(c.pw_hash, old): return err('Old password is wrong')
        problem = password_problem(b.get('new'))
        if problem: return err(problem)
        c.pw_hash = generate_password_hash(b['new']); clear(key); db.session.commit()
        return jsonify(ok=1)

    @app.post('/api/admin/reset-password')
    @admin_only
    def auth_admin_reset():
        """No SMS yet, so the admin helps users who forgot their password."""
        u = db.session.get(User, num(body().get('id')))
        c = db.session.get(Credential, u.id) if u else None
        if not c: return err('This user has no phone login', 404)
        if is_admin(u) and u.id != g.user.id: return err('Cannot reset another admin')
        temp = ''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(8))
        c.pw_hash = generate_password_hash(temp); clear(hkey('ph', c.phone)); db.session.commit()
        return jsonify(phone=c.phone, password=temp)

    def delete_login(uid):
        Credential.query.filter(Credential.user_id == uid).delete(synchronize_session=False)

    def phones_for(ids):
        return {c.user_id: c.phone for c in Credential.query.filter(Credential.user_id.in_(ids or [0])).all()}

    def ids_by_phone(q):
        p = normalize_phone(q)
        return [c.user_id for c in Credential.query.filter(Credential.phone == p).all()] if p else []

    return dict(stored_secret=stored_secret, hit=hit, hkey=hkey, delete_login=delete_login, phones_for=phones_for, ids_by_phone=ids_by_phone)
