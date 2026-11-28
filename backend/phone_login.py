# -*- coding: utf-8 -*-
"""Phone number + password login for Twenty One Party."""
import os, re, json, hmac, secrets, hashlib, urllib.request, urllib.parse
from flask import request, jsonify, g
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

MIN_PASS, MAX_PASS = 6, 72
WINDOW = 15 * 60
PHONE_FAILS = 6        # wrong passwords per phone per window
IP_FAILS = 60          # generous: many mobile users share one IP
IP_SIGNUPS = 15        # new accounts per IP per hour
LINK_TTL = 15 * 60     # time to open Telegram and share the number
OTP_TTL = 10 * 60      # life of the 6-digit code
OTP_TRIES = 5
PENDING_TTL = 48 * 3600  # how long the admin can still approve a waiting signup
HOOK_PATH = '/api/telegram/webhook'
WEAK = {'123456', '1234567', '12345678', '123456789', '000000', '111111', 'password', 'qwerty', 'abc123'}


def normalize_phone(raw):
    """09xxxxxxxx / 07xxxxxxxx / +2519... / 2517...  ->  09xxxxxxxx, else None."""
    p = re.sub(r'[\s\-()]', '', str(raw or ''))
    if p.startswith('+251'): p = '0' + p[4:]
    elif p.startswith('251') and len(p) == 12: p = '0' + p[3:]
    return p if re.fullmatch(r'0[79][0-9]{8}', p) else None


def client_ip():
    # X-Forwarded-For is attacker-controlled unless ProxyFix is configured
    # with an explicit trusted hop count by the deployment.
    return (request.remote_addr or '?')[:45]


def init_auth(app, db, d):
    User, now, tx, err, body, num = d['User'], d['now'], d['tx'], d['err'], d['body'], d['num']
    auth, admin_only, sign, me_dict, cfg = d['auth'], d['admin_only'], d['sign'], d['me_dict'], d['cfg']
    welcome, referral_register, is_admin = d['welcome'], d['referral_register'], d['is_admin']
    BOT_TOKEN, secret = d['bot_token'], d['secret']

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

    class PendingSignup(db.Model):
        __tablename__ = 'pending_signup'
        id = db.Column(db.Integer, primary_key=True)
        token = db.Column(db.String(48), unique=True, nullable=False)   # kept by the app only
        code = db.Column(db.String(32), unique=True, nullable=False)    # inside the Telegram link
        phone = db.Column(db.String(10), index=True, nullable=False)
        name = db.Column(db.String(30)); pw_hash = db.Column(db.String(256)); ref = db.Column(db.String(40))
        created = db.Column(db.Integer, default=now, index=True)
        tg_user = db.Column(db.String(20), index=True)
        otp_hash = db.Column(db.String(64)); otp_expires = db.Column(db.Integer, default=0)
        tries = db.Column(db.Integer, default=0)

    class TgLink(db.Model):
        # one Telegram account can verify only one phone account
        __tablename__ = 'tg_link'
        tg_user = db.Column(db.String(20), primary_key=True)
        user_id = db.Column(db.Integer, unique=True, nullable=False)
        ts = db.Column(db.Integer, default=now)

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

    def verify_on():
        # emergency switch: set PHONE_VERIFY=off in Render to allow signup without Telegram
        return os.environ.get('PHONE_VERIFY', 'on').strip().lower() != 'off'

    def hook_secret():
        return hmac.new(secret(), b'tg-webhook', hashlib.sha256).hexdigest()

    def tg_api(method, **params):
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(f'https://api.telegram.org/bot{BOT_TOKEN}/{method}', data, timeout=8) as r:
            return json.loads(r.read().decode()).get('result')

    bot = {'name': None, 'at': 0}

    def bot_ready():
        '''Returns (bot_username, None) or (None, problem). Connects the bot to this server when it is free.'''
        if bot['name'] and now() - bot['at'] < 600: return bot['name'], None
        # only trust a server address from the environment, never from the request
        base = (os.environ.get('PUBLIC_URL') or os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
        if not BOT_TOKEN: return None, 'BOT_TOKEN is missing'
        if not base.startswith('https://'): return None, 'PUBLIC_URL is missing'
        try:
            name = tg_api('getMe')['username']
            current = (tg_api('getWebhookInfo') or {}).get('url') or ''
            if current and current != base + HOOK_PATH:
                return None, 'The bot is already connected to another server'
            tg_api('setWebhook', url=base + HOOK_PATH, secret_token=hook_secret(), allowed_updates='["message"]')
        except Exception as e:
            print('telegram setup failed:', type(e).__name__)
            return None, 'Cannot reach Telegram'
        bot.update(name=name, at=now())
        return name, None

    def otp_digest(p_id, otp):
        return hmac.new(secret(), f'otp:{p_id}:{otp}'.encode(), hashlib.sha256).hexdigest()

    def create_account(phone, name, pw_hash, start, tg_user=None):
        start = start or ''; ref = ag = None
        if start.startswith('ref_'):
            r = User.query.filter_by(tg_id=start[4:]).first(); ref = r.id if r else None
        elif start.startswith('ag_'):
            a = User.query.filter_by(tg_id=start[3:]).first(); ag = a.id if a and a.is_agent else None
        # random public id: never exposes the phone number and cannot be guessed
        u = User(tg_id='p_' + secrets.token_hex(8), username=name, phone=phone, coins=welcome, referrer_id=ref, agent_id=ag)
        try:
            db.session.add(u); db.session.flush()
            db.session.add(Credential(user_id=u.id, phone=phone, pw_hash=pw_hash))
            if tg_user: db.session.add(TgLink(tg_user=tg_user, user_id=u.id))
            tx(u.id, 'welcome', welcome, 'Welcome bonus')
            if ref: referral_register(ref, u.id)
            if ag: referral_register(ag, u.id)
            PendingSignup.query.filter(PendingSignup.phone == phone).delete(synchronize_session=False)
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); return None
        return u

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
        ref = str(b.get('ref') or '')[:40]
        if not verify_on():
            u = create_account(phone, name, generate_password_hash(pw), ref)
            return session_reply(u) if u else err('This number already has an account. Please log in', 409)
        PendingSignup.query.filter(PendingSignup.created < now() - PENDING_TTL).delete(synchronize_session=False)
        p = PendingSignup(token=secrets.token_urlsafe(24), code=secrets.token_hex(12), phone=phone, name=name,
                          pw_hash=generate_password_hash(pw), ref=ref)
        db.session.add(p); db.session.commit()
        bot_name, problem = bot_ready()
        if problem: print('phone verification unavailable:', problem)
        return jsonify(verify=dict(token=p.token, phone=phone, minutes=LINK_TTL // 60,
                                   link=f'https://t.me/{bot_name}?start=v_{p.code}' if bot_name else ''))

    @app.post('/api/auth/verify')
    def auth_verify():
        b = body(); token = str(b.get('token') or '')[:48]; otp = re.sub(r'\D', '', str(b.get('otp') or ''))[:6]
        p = PendingSignup.query.filter_by(token=token).first() if token else None
        if not p: return err('This request has expired. Sign up again', 410)
        if not p.otp_hash: return err('First open Telegram, press Start and share your number')
        if p.otp_expires < now(): return err('The code has expired. Sign up again', 410)
        if len(otp) != 6: return err('Enter the 6-digit code')
        r = db.session.execute(update(PendingSignup).where(PendingSignup.id == p.id, PendingSignup.tries < OTP_TRIES).values(tries=PendingSignup.tries + 1),
                               execution_options={'synchronize_session': False})
        db.session.commit()
        if r.rowcount != 1: return err('Too many wrong codes. Sign up again', 429)
        p = PendingSignup.query.filter_by(token=token).first()
        if not p or not hmac.compare_digest(p.otp_hash or '', otp_digest(p.id, otp)): return err('Wrong code')
        if db.session.get(TgLink, p.tg_user): return err('This Telegram account was already used for another account', 409)
        u = create_account(p.phone, p.name, p.pw_hash, p.ref, p.tg_user)
        return session_reply(u) if u else err('This number already has an account. Please log in', 409)

    SHARE_BUTTON = json.dumps({'keyboard': [[{'text': '📱 ስልክ ቁጥሬን አጋራ / Share my number', 'request_contact': True}]],
                               'resize_keyboard': True, 'one_time_keyboard': True}, ensure_ascii=False)
    NO_KEYBOARD = json.dumps({'remove_keyboard': True})

    @app.post(HOOK_PATH)
    def telegram_webhook():
        if not BOT_TOKEN or not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token', '').encode(), hook_secret().encode()):
            return err('forbidden', 403)
        msg = (request.get_json(silent=True) or {}).get('message') or {}
        chat = msg.get('chat') or {}; sender = str((msg.get('from') or {}).get('id') or '')
        if chat.get('type') != 'private' or not sender: return jsonify(ok=1)

        def reply(text, keyboard=None):
            # answering inside the webhook response needs no extra call to Telegram
            out = dict(method='sendMessage', chat_id=chat['id'], text=text)
            if keyboard: out['reply_markup'] = keyboard
            return jsonify(out)

        words = str(msg.get('text') or '').split()
        if len(words) == 2 and words[0] == '/start' and words[1].startswith('v_'):
            p = PendingSignup.query.filter(PendingSignup.code == words[1][2:], PendingSignup.created > now() - LINK_TTL).first()
            if not p or (p.tg_user and p.tg_user != sender):
                return reply('⌛ ይህ link ጊዜው አልፏል። ወደ app ተመልሰህ እንደገና ተመዝገብ።\nThis link has expired. Sign up again in the app.')
            if db.session.get(TgLink, sender):
                return reply('⛔ በዚህ Telegram አስቀድሞ account ተከፍቷል።\nThis Telegram account was already used to verify an account.')
            PendingSignup.query.filter(PendingSignup.tg_user == sender, PendingSignup.id != p.id).update(
                {'tg_user': None, 'otp_hash': None}, synchronize_session=False)
            p.tg_user = sender; db.session.commit()
            return reply(f'🔐 Twenty One Party\n\nለ {p.phone} account እየከፈትክ ነው።\nራስህ ካልጀመርከው ምንም አታድርግ።\n\nከታች ያለውን ቁልፍ ተጫን 👇\nOnly continue if YOU started this signup.', SHARE_BUTTON)
        contact = msg.get('contact')
        if contact:
            p = PendingSignup.query.filter(PendingSignup.tg_user == sender, PendingSignup.created > now() - LINK_TTL).order_by(PendingSignup.id.desc()).first()
            if not p: return reply('⌛ መጀመሪያ በ app ውስጥ ያለውን የ Telegram ቁልፍ ተጫን።\nStart again from the app.', NO_KEYBOARD)
            if str(contact.get('user_id') or '') != sender:
                return reply('⛔ የራስህን ቁጥር በቁልፉ ብቻ አጋራ።\nShare YOUR OWN number with the button.', SHARE_BUTTON)
            if normalize_phone(contact.get('phone_number')) != p.phone:
                return reply(f'⛔ የ Telegram ቁጥርህ ከጻፍከው ({p.phone}) የተለየ ነው። በ Telegram ቁጥርህ ተመዝገብ።\nYour Telegram number is different from the number you typed.', NO_KEYBOARD)
            otp = f'{secrets.randbelow(1000000):06d}'
            p.otp_hash = otp_digest(p.id, otp); p.otp_expires = now() + OTP_TTL; p.tries = 0; db.session.commit()
            return reply(f'✅ ቁጥርህ ተረጋግጧል።\n\nኮድ / Code:  {otp}\n\nይህን ኮድ ወደ app አስገባ። ለማንም አትስጥ!\nNever give this code to anyone.', NO_KEYBOARD)
        return reply('👋 Twenty One Party\nለመመዝገብ ከ app ውስጥ Sign up ተጫን።\nTo register, tap Sign up inside the app.')

    @app.get('/api/admin/pending')
    @admin_only
    def auth_pending_list():
        ps = PendingSignup.query.filter(PendingSignup.created > now() - PENDING_TTL).order_by(PendingSignup.id.desc()).limit(50).all()
        taken = {c.phone for c in Credential.query.filter(Credential.phone.in_([p.phone for p in ps] or [''])).all()}
        name, problem = bot_ready() if verify_on() else (None, 'Verification is OFF (PHONE_VERIFY=off)')
        return jsonify(items=[dict(id=p.id, phone=p.phone, name=p.name, age=now() - p.created) for p in ps if p.phone not in taken],
                       bot=name or '', problem=problem or '')

    @app.post('/api/admin/pending')
    @admin_only
    def auth_pending_action():
        '''For people without Telegram: the admin calls the number first, then approves.'''
        b = body(); p = db.session.get(PendingSignup, num(b.get('id')))
        if not p: return err('Request not found', 404)
        if b.get('action') == 'approve':
            if not create_account(p.phone, p.name, p.pw_hash, p.ref): return err('This number already has an account', 409)
        else:
            db.session.delete(p); db.session.commit()
        return jsonify(ok=1)

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
        c.pw_hash = generate_password_hash(temp)
        # Invalidate every previously issued bearer token after an admin reset.
        db.session.execute(update(User).where(User.id == u.id).values(auth_version=User.auth_version + 1),
                           execution_options={'synchronize_session': False})
        clear(hkey('ph', c.phone)); db.session.commit()
        return jsonify(phone=c.phone, password=temp)

    def delete_login(uid):
        TgLink.query.filter(TgLink.user_id == uid).delete(synchronize_session=False)
        Credential.query.filter(Credential.user_id == uid).delete(synchronize_session=False)

    def phones_for(ids):
        return {c.user_id: c.phone for c in Credential.query.filter(Credential.user_id.in_(ids or [0])).all()}

    def ids_by_phone(q):
        p = normalize_phone(q)
        return [c.user_id for c in Credential.query.filter(Credential.phone == p).all()] if p else []

    return dict(verify_on=verify_on, stored_secret=stored_secret, hit=hit, hkey=hkey, delete_login=delete_login, phones_for=phones_for, ids_by_phone=ids_by_phone)
