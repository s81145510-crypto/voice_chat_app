# -*- coding: utf-8 -*-
"""Twenty One Party - PRO backend.
Flask + SQLAlchemy (SQLite for dev, PostgreSQL for production), Agora RTC tokens.
Run:  gunicorn -k gthread -w 2 --threads 16 --timeout 60 main:app
"""
import os, json, hmac, hashlib, time, random, secrets, threading, urllib.request, urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, Response, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import update, func, event, or_, and_, case, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import base64, struct, zlib


class RtcTokenBuilder:
    """Built-in Agora AccessToken (v006) builder - no extra package needed."""

    @staticmethod
    def buildTokenWithUid(app_id, app_cert, channel, uid, role, expire_ts):
        def u16(n): return struct.pack('<H', n)
        def u32(n): return struct.pack('<I', n)
        def pstr(b): return u16(len(b)) + b
        privs = {1: expire_ts}                      # join channel
        if role == 1:                               # publisher
            privs.update({2: expire_ts, 3: expire_ts, 4: expire_ts})
        uid_s = '' if not uid else str(uid)
        salt = random.SystemRandom().randint(1, 99999999)
        msg = u32(salt) + u32(int(time.time()) + 24 * 3600) + u16(len(privs)) + b''.join(u16(k) + u32(v) for k, v in sorted(privs.items()))
        sig = hmac.new(app_cert.encode(), app_id.encode() + channel.encode() + uid_s.encode() + msg, hashlib.sha256).digest()
        content = pstr(sig) + u32(zlib.crc32(channel.encode()) & 0xffffffff) + u32(zlib.crc32(uid_s.encode()) & 0xffffffff) + pstr(msg)
        return '006' + app_id + base64.b64encode(content).decode()

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
BOT_USERNAME = os.environ.get('BOT_USERNAME', '')
ADMIN_IDS = {x.strip() for x in os.environ.get('ADMIN_IDS', '6746329545').split(',') if x.strip()}
AGORA_APP_ID = os.environ.get('AGORA_APP_ID', '')
AGORA_APP_CERT = os.environ.get('AGORA_APP_CERT', '')
PAY_NUMBER = os.environ.get('TELEBIRR_NUMBER', '')
DEV = os.environ.get('DEV_MODE') == '1'
DB_URL = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(DATA_DIR, 'app.db')
if DB_URL.startswith('postgres://'):
    DB_URL = DB_URL.replace('postgres://', 'postgresql://', 1)
IS_SQLITE = DB_URL.startswith('sqlite')
SECRET = hashlib.sha256(('sess:' + (os.environ.get('SECRET_KEY') or BOT_TOKEN or 'dev-secret')).encode()).digest()

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=DB_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    JSON_AS_ASCII=False,
    SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True, 'pool_recycle': 300, 'pool_size': 10, 'max_overflow': 20}
    if not IS_SQLITE else {'pool_pre_ping': True},
)
app.json.ensure_ascii = False
db = SQLAlchemy(app)


@event.listens_for(Engine, 'connect')
def _sqlite_pragmas(con, rec):
    if IS_SQLITE:
        c = con.cursor()
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=15000')
        c.close()


# ----------------------------------------------------------------------------- constants
SEATS = 21  # 0 = host, 1..20 guests
MIN_WITHDRAW = 500
COIN_BIRR = 0.4
WELCOME = 100
MIN_BET, MAX_BET = 10, 5000
LEVELS = [(0, 'Newbie', '🌱'), (500, 'Star', '⭐'), (2000, 'Pro', '💎'), (6000, 'Elite', '🔥'), (20000, 'Legend', '👑'), (60000, 'King', '🦁')]
PACKAGES = [(100, 50), (500, 240), (1000, 470), (2500, 1150), (5000, 2250), (10000, 4400), (25000, 10750), (50000, 21000)]
GIFTS = [  # key, name, emoji, price, tier
    ('rose', 'ጽጌረዳ', '🌹', 10, 'common'), ('heart', 'ልብ', '💖', 20, 'common'), ('coffee', 'ቡና', '☕', 30, 'common'), ('cake', 'ኬክ', '🍰', 50, 'common'),
    ('kiss', 'መሳም', '💋', 80, 'rare'), ('teddy', 'ድብ', '🧸', 120, 'rare'), ('ring', 'ቀለበት', '💍', 200, 'rare'),
    ('flower', 'አበባ', '🌸', 300, 'epic'), ('crown', 'ዘውድ', '👑', 500, 'epic'), ('guitar', 'ጊታር', '🎸', 800, 'epic'),
    ('car', 'መኪና', '🏎️', 1200, 'luxury'), ('jet', 'አውሮፕላን', '✈️', 2000, 'luxury'), ('yacht', 'መርከብ', '🛥️', 3000, 'luxury'), ('castle', 'ቤተ መንግስት', '🏰', 5000, 'luxury'),
    ('rocket', 'ሮኬት', '🚀', 8000, 'legendary'), ('dragon', 'ዘንዶ', '🐉', 12000, 'legendary'), ('phoenix', 'ፊኒክስ', '🔥', 20000, 'legendary'),
    ('galaxy', 'ጋላክሲ', '🌌', 30000, 'mythic'), ('unicorn', 'ዩኒኮርን', '🦄', 40000, 'mythic'), ('universe', 'ዩኒቨርስ', '🪐', 50000, 'mythic'),
]
GIFT_MAP = {k: dict(key=k, name=n, emoji=e, price=p, tier=t) for k, n, e, p, t in GIFTS}
SHARE = {'luxury': 0.70, 'legendary': 0.04, 'mythic': 0.04}  # receiver's share, others 2%  (edit here)
DEFAULT_SHARE = 0.02
WHEEL = [(0, .41), (0.5, .34), (1, .12), (2, .09), (5, .04)]  # RTP ~ 67%
BOX = [(0, .58), (0.5, .20), (1, .09), (2, .07), (5, .05), (10, .01)]  # RTP ~ 68%
P_DICE, P_FLIP, P_RACE = 0.7 / 4.2, 0.7 / 1.4, 0.7 / 4.5
P2P_MULT = 1.9
VIP_PRICE, VIP_DAYS = 1000, 30  # coins for one VIP period (edit here)
VIP_DAILY_BONUS = 1.5           # VIP gets +50% on the daily reward
FREE_ROOMS, VIP_ROOMS = 3, 5    # how many voice rooms one person may keep open
FRAMES = [  # key, name, price in coins (0 = included with VIP), vip_only
    ('neon', 'ኒዮን', 150, False), ('ice', 'በረዶ', 300, False), ('fire', 'እሳት', 400, False),
    ('rose', 'ጽጌረዳ', 600, False), ('rainbow', 'ቀስተ ደመና', 1500, False),
    ('vgold', 'ወርቅ', 0, True), ('vaurora', 'ኦሮራ', 0, True),
]
FRAME_MAP = {k: dict(key=k, name=n, price=p, vip=v) for k, n, p, v in FRAMES}
MISSIONS = [  # key, name, emoji, goal, reward in coins (edit here; VIP gets the VIP_DAILY_BONUS multiplier)
    ('join', 'ወደ ድምፅ/ላይቭ ክፍል ግባ', '🎧', 1, 5),
    ('chat', '5 መልእክት ጻፍ', '💬', 5, 10),
    ('seat', 'ወንበር ላይ ተቀመጥ', '🎤', 1, 10),
    ('gift', 'ስጦታ ላክ', '🎁', 1, 15),
    ('game', '3 ጨዋታ ተጫወት', '🎮', 3, 10),
    ('post', 'ፖስት ጻፍ', '📝', 1, 5),
]
MISSION_MAP = {m[0]: m for m in MISSIONS}
MISSION_BONUS = 30  # extra coins once every mission of the day is claimed
rnd = random.SystemRandom()


# ----------------------------------------------------------------------------- models
def now():
    return int(time.time())


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)  # also the Agora numeric uid
    tg_id = db.Column(db.String(32), unique=True, index=True)
    username = db.Column(db.String(60), default='User')
    coins = db.Column(db.Integer, default=0, nullable=False)
    earnings = db.Column(db.Integer, default=0, nullable=False)
    spent = db.Column(db.Integer, default=0, nullable=False)
    gifts_sent = db.Column(db.Integer, default=0, nullable=False)
    gifts_recv = db.Column(db.Integer, default=0, nullable=False)
    bio = db.Column(db.String(160), default='')
    phone = db.Column(db.String(20), default='')
    tg_photo = db.Column(db.String(300), default='')
    has_photo = db.Column(db.Boolean, default=False)
    photo_v = db.Column(db.Integer, default=0)
    banned = db.Column(db.Boolean, default=False)
    referrer_id = db.Column(db.Integer, nullable=True)
    joined = db.Column(db.Integer, default=now)
    last_daily = db.Column(db.String(10), default='')
    streak = db.Column(db.Integer, default=0)
    vip_until = db.Column(db.Integer, default=0)  # unix time, 0 = not VIP
    frame = db.Column(db.String(20), default='')  # equipped avatar frame
    real_coins = db.Column(db.Integer, default=0, nullable=False)  # coins bought with real money

class Photo(db.Model):
    user_id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.LargeBinary)
    mime = db.Column(db.String(30))


class UserFrame(db.Model):
    user_id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(20), primary_key=True)
    ts = db.Column(db.Integer, default=now)


class MissionProgress(db.Model):
    user_id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.String(10), primary_key=True)  # Ethiopian date, same as today()
    key = db.Column(db.String(12), primary_key=True)  # mission key, or 'all' for the daily bonus
    count = db.Column(db.Integer, default=0, nullable=False)
    claimed = db.Column(db.Boolean, default=False, nullable=False)


class Room(db.Model):
    id = db.Column(db.String(16), primary_key=True)
    name = db.Column(db.String(60))
    topic = db.Column(db.String(100), default='')
    host_id = db.Column(db.Integer, index=True)
    kind = db.Column(db.String(8), default='voice')  # 'voice' | 'live'
    created = db.Column(db.Integer, default=now)
    last_active = db.Column(db.Integer, default=now, index=True)
    version = db.Column(db.Integer, default=0, nullable=False)


class Seat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(16), index=True)
    idx = db.Column(db.Integer)
    user_id = db.Column(db.Integer)
    muted = db.Column(db.Boolean, default=False)
    __table_args__ = (db.UniqueConstraint('room_id', 'idx'), db.UniqueConstraint('room_id', 'user_id'))


class Presence(db.Model):
    room_id = db.Column(db.String(16), primary_key=True)
    user_id = db.Column(db.Integer, primary_key=True)
    seen = db.Column(db.Integer, index=True)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(16), index=True)
    user_id = db.Column(db.Integer)
    name = db.Column(db.String(60))
    text = db.Column(db.String(300), default='')
    kind = db.Column(db.String(8), default='chat')
    meta = db.Column(db.Text, default='')
    ts = db.Column(db.Integer, default=now, index=True)


class Tx(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    kind = db.Column(db.String(20))
    amount = db.Column(db.Integer)
    note = db.Column(db.String(120), default='')
    ts = db.Column(db.Integer, default=now)


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    username = db.Column(db.String(60))
    coins = db.Column(db.Integer)
    birr = db.Column(db.Integer)
    ref = db.Column(db.String(40), unique=True)
    status = db.Column(db.String(10), default='pending', index=True)
    ts = db.Column(db.Integer, default=now)


class Shot(db.Model):
    purchase_id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.LargeBinary)
    mime = db.Column(db.String(30))


class Withdraw(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    username = db.Column(db.String(60))
    coins = db.Column(db.Integer)
    birr = db.Column(db.Integer)
    phone = db.Column(db.String(20))
    status = db.Column(db.String(10), default='pending', index=True)
    ts = db.Column(db.Integer, default=now)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    username = db.Column(db.String(60))
    text = db.Column(db.String(500))
    likes = db.Column(db.Integer, default=0)
    ts = db.Column(db.Integer, default=now, index=True)


class PostLike(db.Model):
    post_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, primary_key=True)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)
    username = db.Column(db.String(60))
    title = db.Column(db.String(80))
    desc = db.Column(db.String(400))
    contact = db.Column(db.String(80))
    ts = db.Column(db.Integer, default=now, index=True)


class P2P(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, index=True)
    creator_name = db.Column(db.String(60))
    bet = db.Column(db.Integer)
    status = db.Column(db.String(8), default='open', index=True)
    ts = db.Column(db.Integer, default=now)


def ensure_columns():
    """db.create_all() creates missing tables but never adds columns to old ones - add them here."""
    from sqlalchemy import inspect
    want = {User: [('vip_until', 'INTEGER DEFAULT 0'), ('frame', "VARCHAR(20) DEFAULT ''"), ('real_coins', 'INTEGER DEFAULT 0 NOT NULL')],
            Room: [('kind', "VARCHAR(8) DEFAULT 'voice'")]}
    insp = inspect(db.engine)
    for M, cols in want.items():
        tbl = M.__tablename__
        have = {c['name'] for c in insp.get_columns(tbl)}
        for name, ddl in cols:
            if name in have:
                continue
            try:
                db.session.execute(sa_text(f'ALTER TABLE "{tbl}" ADD COLUMN {name} {ddl}'))
                db.session.commit()
            except Exception as e:  # the other worker added it first
                db.session.rollback()
                print('ensure_columns:', tbl, name, e)


_init_lock = threading.Lock()
with app.app_context():
    with _init_lock:
        try:
            db.create_all()
        except Exception as e:  # another worker created tables at the same moment
            print('create_all:', e)
        try:
            ensure_columns()
        except Exception as e:
            print('ensure_columns:', e)


# ----------------------------------------------------------------------------- helpers
def err(msg, code=400):
    return jsonify(error=msg), code


def body():
    return request.get_json(silent=True) or {}


def num(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


def sign(uid, days=30):
    p = f'{uid}.{now() + days * 86400}'
    return p + '.' + hmac.new(SECRET, p.encode(), hashlib.sha256).hexdigest()[:32]


def parse_token(t):
    try:
        uid, exp, sig = t.split('.')
        ok = hmac.compare_digest(sig, hmac.new(SECRET, f'{uid}.{exp}'.encode(), hashlib.sha256).hexdigest()[:32])
        return int(uid) if ok and int(exp) > now() else None
    except Exception:
        return None


def verify_init(init):
    data = dict(parse_qsl(init, keep_blank_values=True))
    h = data.pop('hash', None)
    if not h or not BOT_TOKEN:
        return None
    check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
    key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h):
        return None
    if now() - num(data.get('auth_date')) > 86400 * 3:
        return None
    try:
        return json.loads(data['user']), data
    except Exception:
        return None


def auth(f):
    @wraps(f)
    def w(*a, **k):
        h = request.headers.get('Authorization', '')
        uid = parse_token(h[7:]) if h.startswith('Bearer ') else None
        u = db.session.get(User, uid) if uid else None
        if not u:
            return err('unauthorized', 401)
        if u.banned:
            return err('መለያህ ታግዷል', 403)
        g.user = u
        return f(*a, **k)
    return w


def admin_only(f):
    @wraps(f)
    @auth
    def w(*a, **k):
        if g.user.tg_id not in ADMIN_IDS:
            return err('forbidden', 403)
        return f(*a, **k)
    return w


_rl = {}


def limited(key, gap):
    t = time.time()
    k = f'{g.user.id}:{key}'
    if t - _rl.get(k, 0) < gap:
        return True
    _rl[k] = t
    if len(_rl) > 60000:
        _rl.clear()
    return False


def level_of(spent):
    i = 0
    for n, (t, _, _) in enumerate(LEVELS):
        if spent >= t:
            i = n
    return i


def photo_url(u):
    return f'/api/photo/{u.id}?v={u.photo_v}' if u.has_photo else (u.tg_photo or '')


def vip_active(u):
    return (u.vip_until or 0) > now()


def frame_of(u):
    """The frame other people see: VIP-only frames disappear while VIP is expired."""
    f = FRAME_MAP.get(u.frame or '')
    return f['key'] if f and (not f['vip'] or vip_active(u)) else ''


def pub(u):
    lv = level_of(u.spent)
    return dict(id=u.id, name=u.username, photo=photo_url(u), lv=lv, lvn=LEVELS[lv][1], lvi=LEVELS[lv][2], bio=u.bio or '',
                vip=vip_active(u), fr=frame_of(u))


def me_dict(u):
    d = pub(u)
    nxt = LEVELS[d['lv'] + 1][0] if d['lv'] + 1 < len(LEVELS) else None
    d.update(coins=u.coins, earnings=u.earnings, spent=u.spent, phone=u.phone or '', tg_id=u.tg_id, is_admin=u.tg_id in ADMIN_IDS,
             sent=u.gifts_sent, recv=u.gifts_recv, next=nxt, base=LEVELS[d['lv']][0], streak=u.streak,
             daily_ready=u.last_daily != today(), vip_until=u.vip_until or 0)
    return d


def today():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%Y-%m-%d')  # Ethiopia UTC+3


def coins_of(uid):
    return db.session.query(User.coins).filter(User.id == uid).scalar() or 0


def spend(uid, n):
    r = db.session.execute(update(User).where(User.id == uid, User.coins >= n).values(coins=User.coins - n),
                           execution_options={'synchronize_session': False})
    return r.rowcount == 1


def add_coins(uid, n):
    db.session.execute(update(User).where(User.id == uid).values(coins=User.coins + n), execution_options={'synchronize_session': False})


def tx(uid, kind, amt, note=''):
    db.session.add(Tx(user_id=uid, kind=kind, amount=amt, note=note[:120]))


def notify(chat_id, text):
    if not BOT_TOKEN or not chat_id:
        return

    def run():
        try:
            data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
            urllib.request.urlopen(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data, timeout=8)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def notify_admins(text):
    for a in ADMIN_IDS:
        notify(a, text)


def sniff_image(b):
    if b[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return 'image/webp'
    return None


@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options'] = 'nosniff'
    if request.path.startswith('/api/') and 'Cache-Control' not in r.headers:
        r.headers['Cache-Control'] = 'no-store'
    return r


@app.errorhandler(413)
def too_big(e):
    return err('ፋይሉ በጣም ትልቅ ነው', 413)


# ----------------------------------------------------------------------------- pages / auth
_TPL = next((p for p in (os.path.join(BASE, 'templates', 'index.html'), os.path.join(BASE, '..', 'templates', 'index.html'))
             if os.path.exists(p)), os.path.join(BASE, 'templates', 'index.html'))
_HTML = open(_TPL, encoding='utf-8').read()


@app.get('/')
def index():
    return Response(_HTML, mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/healthz')
def healthz():
    db.session.execute(sa_text('select 1'))
    return 'ok'


def cfg():
    return dict(agora_app_id=AGORA_APP_ID, gifts=GIFTS_PUB, packages=[dict(coins=c, birr=b) for c, b in PACKAGES], levels=LEVELS,
                pay_number=PAY_NUMBER, bot=BOT_USERNAME, min_withdraw=MIN_WITHDRAW, rate=COIN_BIRR, seats=SEATS,
                min_bet=MIN_BET, max_bet=MAX_BET, p2p_mult=P2P_MULT, frames=FRAMES_PUB,
                vip=dict(price=VIP_PRICE, days=VIP_DAYS, bonus=VIP_DAILY_BONUS, rooms=VIP_ROOMS))


GIFTS_PUB = [GIFT_MAP[k] for k, *_ in GIFTS]
FRAMES_PUB = list(FRAME_MAP.values())


@app.post('/api/register')
def register():
    b = body()
    init = b.get('init') or ''
    start = ''
    if init:
        v = verify_init(init)
        if not v:
            return err('invalid telegram data', 401)
        tu, data = v
        tgid = str(tu['id'])
        name = ((tu.get('first_name') or '') + ' ' + (tu.get('last_name') or '')).strip() or tu.get('username') or 'User'
        photo = tu.get('photo_url') or ''
        start = data.get('start_param') or ''
    elif DEV:
        tgid = str(b.get('dev_id') or '1'); name = 'Dev ' + tgid; photo = ''
    else:
        return err('Telegram ውስጥ ክፈት', 401)
    u = User.query.filter_by(tg_id=tgid).first()
    if not u:
        ref = None
        if start.startswith('ref_'):
            r = User.query.filter_by(tg_id=start[4:]).first()
            ref = r.id if r else None
        u = User(tg_id=tgid, username=name[:40], coins=WELCOME, tg_photo=photo, referrer_id=ref)
        db.session.add(u)
        try:
            db.session.flush()
            tx(u.id, 'welcome', WELCOME, 'Welcome bonus')
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            u = User.query.filter_by(tg_id=tgid).first()
    elif photo and u.tg_photo != photo:
        u.tg_photo = photo
        db.session.commit()
    if u.banned:
        return err('መለያህ ታግዷል', 403)
    return jsonify(token=sign(u.id), me=me_dict(u), cfg=cfg())


@app.get('/api/me')
@auth
def me():
    return jsonify(me=me_dict(g.user))


@app.post('/api/profile')
@auth
def profile():
    b = body()
    u = g.user
    if 'name' in b:
        n = str(b['name']).strip()[:30]
        if len(n) < 2:
            return err('ስም አጭር ነው')
        u.username = n
    if 'bio' in b:
        u.bio = str(b['bio']).strip()[:160]
    if 'phone' in b:
        p = str(b['phone']).strip()
        if p and not (p.isdigit() and len(p) == 10 and p[:2] in ('09', '07')):
            return err('ስልክ ቁጥር 09xxxxxxxx መሆን አለበት')
        u.phone = p
    db.session.commit()
    return jsonify(me=me_dict(u))


@app.post('/api/profile/photo')
@auth
def profile_photo():
    f = request.files.get('file')
    data = f.read() if f else b''
    mime = sniff_image(data)
    if not mime or len(data) > 1024 * 1024:
        return err('ምስል (jpg/png/webp) ከ1MB በታች ይሁን')
    p = db.session.get(Photo, g.user.id)
    if p:
        p.data, p.mime = data, mime
    else:
        db.session.add(Photo(user_id=g.user.id, data=data, mime=mime))
    g.user.has_photo = True
    g.user.photo_v = (g.user.photo_v or 0) + 1
    db.session.commit()
    return jsonify(me=me_dict(g.user))


@app.get('/api/photo/<int:uid>')
def photo(uid):
    p = db.session.get(Photo, uid)
    if not p:
        return Response(status=404)
    return Response(p.data, mimetype=p.mime, headers={'Cache-Control': 'public, max-age=86400'})


@app.post('/api/daily')
@auth
def daily():
    u = g.user
    t = today()
    if u.last_daily == t:
        return err('ዛሬ ወስደሃል፤ ነገ ተመለስ')
    y = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=1)).strftime('%Y-%m-%d')
    streak = (u.streak or 0) + 1 if u.last_daily == y else 1
    reward = 20 + 5 * min(streak - 1, 8)
    if vip_active(u):
        reward = int(reward * VIP_DAILY_BONUS)
    r = db.session.execute(update(User).where(User.id == u.id, User.last_daily != t).values(last_daily=t, streak=streak, coins=User.coins + reward),
                           execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        return err('ዛሬ ወስደሃል')
    tx(u.id, 'daily', reward, f'Day {streak}')
    db.session.commit()
    db.session.refresh(u)
    return jsonify(reward=reward, streak=streak, me=me_dict(u))


_lb = {'t': 0, 'd': None}


@app.get('/api/leaderboard')
@auth
def leaderboard():
    if now() - _lb['t'] > 30 or not _lb['d']:
        top_s = User.query.filter(User.gifts_sent > 0, User.banned == False).order_by(User.gifts_sent.desc()).limit(10).all()  # noqa
        top_r = User.query.filter(User.gifts_recv > 0, User.banned == False).order_by(User.gifts_recv.desc()).limit(10).all()  # noqa
        _lb['d'] = dict(senders=[dict(pub(u), v=u.gifts_sent) for u in top_s], receivers=[dict(pub(u), v=u.gifts_recv) for u in top_r])
        _lb['t'] = now()
    return jsonify(_lb['d'])


# ----------------------------------------------------------------------------- rooms
_last_clean = {}
_last_gc = {'t': 0}


def bump(rid):
    db.session.execute(update(Room).where(Room.id == rid).values(version=Room.version + 1), execution_options={'synchronize_session': False})
    db.session.commit()


def get_room(rid):
    return db.session.get(Room, rid)


def touch(rid, uid):
    t = now()
    p = db.session.get(Presence, (rid, uid))
    try:
        if not p:
            db.session.add(Presence(room_id=rid, user_id=uid, seen=t))
            db.session.commit()
        elif t - p.seen >= 8:
            p.seen = t
            db.session.commit()
    except IntegrityError:
        db.session.rollback()


def online_count(rid):
    return db.session.query(func.count()).select_from(Presence).filter(Presence.room_id == rid, Presence.seen > now() - 20).scalar() or 0


def clean_room(rid):
    t = now()
    if t - _last_clean.get(rid, 0) < 10:
        return
    _last_clean[rid] = t
    if len(_last_clean) > 5000:
        _last_clean.clear()
    stale = [x for (x,) in db.session.query(Presence.user_id).filter(Presence.room_id == rid, Presence.seen < t - 30)]
    if stale:
        n = Seat.query.filter(Seat.room_id == rid, Seat.user_id.in_(stale)).delete(synchronize_session=False)
        Presence.query.filter(Presence.room_id == rid, Presence.user_id.in_(stale)).delete(synchronize_session=False)
        db.session.commit()
        if n:
            bump(rid)
    room = db.session.get(Room, rid)
    if room and room.kind == 'live' and t - room.created > 30 and not Seat.query.filter_by(room_id=rid, idx=0).first():
        delete_room(rid)  # host disconnected -> live ended
        return
    Message.query.filter(Message.room_id == rid, Message.ts < t - 7200).delete(synchronize_session=False)
    if online_count(rid) > 0:
        db.session.execute(update(Room).where(Room.id == rid).values(last_active=t))
    db.session.commit()


def seats_payload(rid):
    rows = db.session.query(Seat, User).join(User, User.id == Seat.user_id).filter(Seat.room_id == rid).all()
    return [dict(i=s.idx, u=pub(u), m=bool(s.muted)) for s, u in rows]


def gc_rooms():
    t = now()
    if t - _last_gc['t'] < 120:
        return
    _last_gc['t'] = t
    old = [r for (r,) in db.session.query(Room.id).filter(Room.last_active < t - 3 * 86400)]
    for rid in old:
        delete_room(rid)


def delete_room(rid):
    for M in (Seat, Presence, Message):
        M.query.filter(M.room_id == rid).delete(synchronize_session=False)
    Room.query.filter(Room.id == rid).delete(synchronize_session=False)
    db.session.commit()


@app.get('/api/rooms')
@auth
def rooms():
    gc_rooms()
    t = now()
    rs = Room.query.filter(or_(and_(Room.kind == 'live', Room.last_active > t - 90), and_(or_(Room.kind == 'voice', Room.kind.is_(None)), Room.last_active > t - 6 * 3600))).order_by(Room.last_active.desc()).limit(100).all()
    on = dict(db.session.query(Presence.room_id, func.count()).filter(Presence.seen > t - 20).group_by(Presence.room_id).all())
    st = dict(db.session.query(Seat.room_id, func.count()).group_by(Seat.room_id).all())
    hosts = {u.id: u for u in User.query.filter(User.id.in_([r.host_id for r in rs] or [0])).all()}
    out = [dict(id=r.id, kind=r.kind or 'voice', name=r.name, topic=r.topic, online=on.get(r.id, 0), seated=st.get(r.id, 0), host=pub(hosts[r.host_id]) if r.host_id in hosts else None) for r in rs]
    out.sort(key=lambda x: -x['online'])
    return jsonify(rooms=out)


@app.post('/api/rooms/create')
@auth
def room_create():
    b = body()
    name = str(b.get('name', '')).strip()[:40]
    if len(name) < 2:
        return err('የክፍል ስም ያስገቡ')
    kind = 'live' if b.get('kind') == 'live' else 'voice'
    if kind == 'live':
        for (old,) in db.session.query(Room.id).filter(Room.host_id == g.user.id, Room.kind == 'live').all():
            delete_room(old)
    elif Room.query.filter(Room.host_id == g.user.id, Room.kind != 'live').count() >= (VIP_ROOMS if vip_active(g.user) else FREE_ROOMS):
        return err(f'ከ{VIP_ROOMS if vip_active(g.user) else FREE_ROOMS} በላይ ክፍል መክፈት አይቻልም')
    r = Room(id=secrets.token_hex(4), name=name, topic=str(b.get('topic', '')).strip()[:100], host_id=g.user.id, kind=kind)
    db.session.add(r)
    db.session.commit()
    return jsonify(id=r.id)


@app.post('/api/rooms/<rid>/join')
@auth
def room_join(rid):
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    uid = g.user.id
    Seat.query.filter(Seat.user_id == uid, Seat.room_id != rid).delete(synchronize_session=False)
    Presence.query.filter(Presence.user_id == uid, Presence.room_id != rid).delete(synchronize_session=False)
    db.session.commit()
    touch(rid, uid)
    if uid == r.host_id and not Seat.query.filter_by(room_id=rid, user_id=uid).first():
        try:
            Seat.query.filter_by(room_id=rid, idx=0).delete()
            db.session.add(Seat(room_id=rid, idx=0, user_id=uid))
            db.session.commit()
            bump(rid)
        except IntegrityError:
            db.session.rollback()
    db.session.execute(update(Room).where(Room.id == rid).values(last_active=now()))
    db.session.commit()
    mission_inc(uid, 'join')
    return jsonify(room=dict(id=r.id, kind=r.kind or 'voice', name=r.name, topic=r.topic, host_id=r.host_id))


@app.get('/api/rooms/<rid>/state')
@auth
def room_state(rid):
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    touch(rid, g.user.id)
    clean_room(rid)
    db.session.refresh(r)
    cv, mid = num(request.args.get('v'), -1), num(request.args.get('mid'))
    out = dict(v=r.version, online=online_count(rid))
    if cv != r.version:
        out['seats'] = seats_payload(rid)
    q = Message.query.filter(Message.room_id == rid)
    if mid <= 0:
        ms = list(reversed(q.order_by(Message.id.desc()).limit(30).all()))
    else:
        ms = q.filter(Message.id > mid).order_by(Message.id).limit(60).all()
    vips = set()
    if ms:
        vips = {i for (i,) in db.session.query(User.id).filter(User.id.in_({m.user_id for m in ms}), User.vip_until > now())}
    out['msgs'] = [dict(id=m.id, u=m.user_id, n=m.name, t=m.text, k=m.kind, v=m.user_id in vips,
                        meta=json.loads(m.meta) if m.meta else None) for m in ms]
    return jsonify(out)


@app.post('/api/rooms/<rid>/seat')
@auth
def take_seat(rid):
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    idx = num(body().get('idx'), -1)
    if not 0 <= idx < SEATS:
        return err('bad seat')
    if (idx == 0) != (g.user.id == r.host_id):
        return err('ቁጥር 0 ወንበር ለhost ብቻ ነው' if idx == 0 else 'host በራሱ ወንበር ላይ ነው')
    try:
        Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete()
        db.session.add(Seat(room_id=rid, idx=idx, user_id=g.user.id))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return err('ወንበሩ ተይዟል', 409)
    touch(rid, g.user.id)
    bump(rid)
    if idx != 0:
        mission_inc(g.user.id, 'seat')
    return jsonify(ok=1)


@app.post('/api/rooms/<rid>/leave-seat')
@auth
def leave_seat(rid):
    n = Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete()
    db.session.commit()
    if n:
        bump(rid)
    return jsonify(ok=1)


@app.post('/api/rooms/<rid>/exit')
@auth
def room_exit(rid):
    r = get_room(rid)
    if r and r.kind == 'live' and r.host_id == g.user.id:
        delete_room(rid)  # host ends the live
        return jsonify(ok=1)
    n = Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete()
    Presence.query.filter_by(room_id=rid, user_id=g.user.id).delete()
    db.session.commit()
    if n:
        bump(rid)
    return jsonify(ok=1)


@app.post('/api/rooms/<rid>/seat-action')
@auth
def seat_action(rid):
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    if g.user.id != r.host_id and g.user.tg_id not in ADMIN_IDS:
        return err('host ብቻ', 403)
    b = body()
    uid, act = num(b.get('uid')), b.get('action')
    if uid == r.host_id:
        return err('host ላይ አይቻልም')
    s = Seat.query.filter_by(room_id=rid, user_id=uid).first()
    if not s:
        return err('ተጠቃሚው ወንበር ላይ የለም')
    if act == 'kick':
        db.session.delete(s)
    elif act in ('mute', 'unmute'):
        s.muted = act == 'mute'
    else:
        return err('bad action')
    db.session.commit()
    bump(rid)
    return jsonify(ok=1)


@app.post('/api/rooms/<rid>/chat')
@auth
def room_chat(rid):
    if not get_room(rid):
        return err('ROOM_GONE', 404)
    t = str(body().get('text', '')).strip()[:200]
    if not t:
        return err('empty')
    if limited('chat', 0.8):
        return err('ቀስ በል 🙂', 429)
    db.session.add(Message(room_id=rid, user_id=g.user.id, name=g.user.username, text=t))
    db.session.commit()
    mission_inc(g.user.id, 'chat')
    return jsonify(ok=1)


@app.post('/api/rooms/<rid>/gift')
@auth
def room_gift(rid):
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    b = body()
    gift = GIFT_MAP.get(b.get('gift'))
    qty = max(1, min(99, num(b.get('qty'), 1)))
    to = num(b.get('to'))
    if not gift or to == g.user.id:
        return err('bad request')
    if to != r.host_id and not Seat.query.filter_by(room_id=rid, user_id=to).first():
        return err('ተቀባዩ ወንበር ላይ መሆን አለበት')
    target = db.session.get(User, to)
    if not target:
        return err('user not found')
    if limited('gift', 0.4):
        return err('ቀስ በል', 429)
    total = gift['price'] * qty
    if not spend(g.user.id, total):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    earn = int(total * SHARE.get(gift['tier'], DEFAULT_SHARE))
    db.session.execute(update(User).where(User.id == to).values(earnings=User.earnings + earn, gifts_recv=User.gifts_recv + total),
                       execution_options={'synchronize_session': False})
    db.session.execute(update(User).where(User.id == g.user.id).values(spent=User.spent + total, gifts_sent=User.gifts_sent + total),
                       execution_options={'synchronize_session': False})
    tx(g.user.id, 'gift', -total, f'{gift["key"]}x{qty} -> {to}')
    if earn:
        tx(to, 'earn', earn, f'gift from {g.user.id}')
    meta = dict(gift=gift['key'], emoji=gift['emoji'], qty=qty, tier=gift['tier'], to=to, to_name=target.username, price=total)
    db.session.add(Message(room_id=rid, user_id=g.user.id, name=g.user.username, kind='gift', meta=json.dumps(meta, ensure_ascii=False)))
    db.session.commit()
    mission_inc(g.user.id, 'gift')
    return jsonify(coins=coins_of(g.user.id))


@app.post('/api/agora-token')
@auth
def agora_token():
    rid = body().get('room_id')
    r = get_room(rid)
    if not r:
        return err('ROOM_GONE', 404)
    if not AGORA_APP_ID:
        return err('Agora አልተዋቀረም (AGORA_APP_ID)', 500)
    seated = bool(Seat.query.filter_by(room_id=rid, user_id=g.user.id).first())
    channel = 'room_' + rid
    token = None
    if AGORA_APP_CERT:
        exp = now() + 3600
        token = RtcTokenBuilder.buildTokenWithUid(AGORA_APP_ID, AGORA_APP_CERT, channel, g.user.id, 1 if seated else 2, exp)
    return jsonify(app_id=AGORA_APP_ID, channel=channel, token=token, uid=g.user.id, publisher=seated)


# ----------------------------------------------------------------------------- VIP & frames
def seats_changed(uid):
    """Make the rooms this user sits in re-send their seats, so a new frame shows up at once."""
    for (rid,) in db.session.query(Seat.room_id).filter(Seat.user_id == uid).all():
        bump(rid)


def shop_state(u):
    owned = [k for (k,) in db.session.query(UserFrame.key).filter(UserFrame.user_id == u.id)]
    return dict(me=me_dict(u), owned=owned)


@app.get('/api/shop')
@auth
def shop():
    return jsonify(shop_state(g.user))


@app.post('/api/vip/buy')
@auth
def vip_buy():
    u = g.user
    if limited('shop', 1.5):
        return err('ቀስ በል', 429)
    if not spend(u.id, VIP_PRICE):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    t = now()
    # still VIP -> extend from the current end date, otherwise start from now (one atomic UPDATE)
    db.session.execute(update(User).where(User.id == u.id).values(
        vip_until=case((User.vip_until > t, User.vip_until + VIP_DAYS * 86400), else_=t + VIP_DAYS * 86400),
        spent=User.spent + VIP_PRICE), execution_options={'synchronize_session': False})
    tx(u.id, 'vip', -VIP_PRICE, f'VIP {VIP_DAYS}d')
    db.session.commit()
    db.session.refresh(u)
    seats_changed(u.id)
    return jsonify(shop_state(u))


@app.post('/api/frame/buy')
@auth
def frame_buy():
    u = g.user
    f = FRAME_MAP.get(str(body().get('key') or ''))
    if not f:
        return err('bad request')
    if f['vip']:
        return err('ይህ ፍሬም ለVIP ብቻ ነው')
    if db.session.get(UserFrame, (u.id, f['key'])):
        return err('ይህ ፍሬም አለህ')
    if limited('shop', 1.5):
        return err('ቀስ በል', 429)
    if not spend(u.id, f['price']):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    db.session.add(UserFrame(user_id=u.id, key=f['key']))
    db.session.execute(update(User).where(User.id == u.id).values(frame=f['key'], spent=User.spent + f['price']),
                       execution_options={'synchronize_session': False})
    tx(u.id, 'frame', -f['price'], f['key'])
    try:
        db.session.commit()
    except IntegrityError:  # double tap: the coins are rolled back with it
        db.session.rollback()
        return err('ይህ ፍሬም አለህ')
    db.session.refresh(u)
    seats_changed(u.id)
    return jsonify(shop_state(u))


@app.post('/api/frame/equip')
@auth
def frame_equip():
    u = g.user
    key = str(body().get('key') or '')  # '' = take the frame off
    if key:
        f = FRAME_MAP.get(key)
        if not f:
            return err('bad request')
        if f['vip']:
            if not vip_active(u):
                return err('ይህ ፍሬም ለVIP ብቻ ነው')
        elif not db.session.get(UserFrame, (u.id, key)):
            return err('መጀመሪያ ግዛው')
    if limited('equip', 0.5):
        return err('ቀስ በል', 429)
    u.frame = key
    db.session.commit()
    seats_changed(u.id)
    return jsonify(shop_state(u))


# ----------------------------------------------------------------------------- daily missions
_mgc = {'t': 0}


def mission_reward(u, base):
    return int(base * VIP_DAILY_BONUS) if vip_active(u) else base


def mission_inc(uid, key, n=1):
    """Count progress for today's missions. Never lets a failure here break the real action."""
    if key not in MISSION_MAP:
        return
    d = today()

    def bump_row():
        r = db.session.execute(update(MissionProgress).where(MissionProgress.user_id == uid, MissionProgress.day == d, MissionProgress.key == key)
                               .values(count=MissionProgress.count + n), execution_options={'synchronize_session': False})
        return r.rowcount

    try:
        if bump_row() == 0:
            db.session.add(MissionProgress(user_id=uid, day=d, key=key, count=n, claimed=False))
        db.session.commit()
    except IntegrityError:  # two requests created the row at the same moment
        db.session.rollback()
        try:
            bump_row()
            db.session.commit()
        except Exception:
            db.session.rollback()
    except Exception:
        db.session.rollback()


def missions_state(u):
    rows = {m.key: m for m in MissionProgress.query.filter_by(user_id=u.id, day=today()).all()}
    items = []
    for key, name, emoji, goal, reward in MISSIONS:
        r = rows.get(key)
        items.append(dict(key=key, name=name, emoji=emoji, goal=goal, reward=mission_reward(u, reward),
                          count=min(r.count if r else 0, goal), claimed=bool(r and r.claimed)))
    bonus = rows.get('all')
    return dict(items=items, coins=coins_of(u.id),
                bonus=dict(reward=mission_reward(u, MISSION_BONUS), ready=all(i['claimed'] for i in items) and not bonus, claimed=bool(bonus)))


@app.get('/api/missions')
@auth
def missions():
    t = now()
    if t - _mgc['t'] > 6 * 3600:  # forget old days now and then
        _mgc['t'] = t
        cutoff = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=3)).strftime('%Y-%m-%d')
        MissionProgress.query.filter(MissionProgress.day < cutoff).delete(synchronize_session=False)
        db.session.commit()
    return jsonify(missions_state(g.user))


@app.post('/api/missions/claim')
@auth
def mission_claim():
    u = g.user
    key, d = str(body().get('key') or ''), today()
    if limited('mclaim', 0.5):
        return err('ቀስ በል', 429)
    if key == 'all':
        b = missions_state(u)['bonus']
        if not b['ready']:
            return err('መጀመሪያ ሁሉንም ተልእኮ ውሰድ')
        reward = b['reward']
        db.session.add(MissionProgress(user_id=u.id, day=d, key='all', count=1, claimed=True))
        try:
            db.session.flush()
        except IntegrityError:  # primary key stops a double claim
            db.session.rollback()
            return err('ዛሬ ወስደሃል')
    else:
        m = MISSION_MAP.get(key)
        if not m:
            return err('bad request')
        reward = mission_reward(u, m[4])
        r = db.session.execute(update(MissionProgress).where(MissionProgress.user_id == u.id, MissionProgress.day == d, MissionProgress.key == key,
                                                             MissionProgress.claimed == False, MissionProgress.count >= m[3])  # noqa
                               .values(claimed=True), execution_options={'synchronize_session': False})
        if r.rowcount != 1:
            db.session.rollback()
            return err('ገና አልተጠናቀቀም ወይም ወስደሃል')
    add_coins(u.id, reward)
    tx(u.id, 'mission', reward, key)
    db.session.commit()
    out = missions_state(u)
    out['got'] = reward
    return jsonify(out)


# ----------------------------------------------------------------------------- wallet
@app.get('/api/wallet')
@auth
def wallet():
    u = g.user
    P = Purchase.query.filter_by(user_id=u.id).order_by(Purchase.id.desc()).limit(15).all()
    W = Withdraw.query.filter_by(user_id=u.id).order_by(Withdraw.id.desc()).limit(15).all()
    T = Tx.query.filter_by(user_id=u.id).order_by(Tx.id.desc()).limit(30).all()
    return jsonify(me=me_dict(u),
                   purchases=[dict(id=p.id, coins=p.coins, birr=p.birr, status=p.status, ts=p.ts) for p in P],
                   withdraws=[dict(id=w.id, coins=w.coins, birr=w.birr, status=w.status, ts=w.ts) for w in W],
                   txs=[dict(kind=t.kind, amount=t.amount, note=t.note, ts=t.ts) for t in T])


@app.post('/api/coins/buy')
@auth
def coins_buy():
    pkg = num(request.form.get('coins'))
    ref = (request.form.get('ref') or '').strip().upper()[:40]
    price = dict(PACKAGES).get(pkg)
    if not price:
        return err('ፓኬጅ ይምረጡ')
    if len(ref) < 6:
        return err('የTelebirr ግብይት ቁጥር ያስገቡ')
    f = request.files.get('file')
    data = f.read() if f else b''
    mime = sniff_image(data)
    if not mime:
        return err('የክፍያ ስክሪንሾት ያያይዙ')
    if Purchase.query.filter_by(user_id=g.user.id, status='pending').count() >= 3:
        return err('3 በመጠባበቅ ላይ ያሉ ጥያቄዎች አሉ')
    p = Purchase(user_id=g.user.id, username=g.user.username, coins=pkg, birr=price, ref=ref)
    db.session.add(p)
    try:
        db.session.flush()
        db.session.add(Shot(purchase_id=p.id, data=data, mime=mime))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return err('ይህ የግብይት ቁጥር ቀድሞ ተመዝግቧል')
    notify_admins(f'🛒 አዲስ ግዢ: {g.user.username} — {pkg} coins / {price} Birr\nRef: {ref}')
    return jsonify(ok=1)


@app.post('/api/withdraw')
@auth
def withdraw():
    b = body()
    coins = num(b.get('coins'))
    phone = str(b.get('phone') or g.user.phone or '').strip()
    if coins < MIN_WITHDRAW:
        return err(f'ዝቅተኛ ማውጣት {MIN_WITHDRAW} coins')
    if not (phone.isdigit() and len(phone) == 10):
        return err('ትክክለኛ ስልክ ቁጥር ያስገቡ')
    r = db.session.execute(update(User).where(User.id == g.user.id, User.earnings >= coins).values(earnings=User.earnings - coins),
                           execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback()
        return err('ገቢህ አይበቃም')
    w = Withdraw(user_id=g.user.id, username=g.user.username, coins=coins, birr=int(coins * COIN_BIRR), phone=phone)
    db.session.add(w)
    tx(g.user.id, 'withdraw', -coins, 'withdraw request')
    db.session.commit()
    notify_admins(f'💸 ማውጣት: {g.user.username} — {coins} coins = {w.birr} Birr → {phone}')
    return jsonify(ok=1)


# ----------------------------------------------------------------------------- community
@app.get('/api/posts')
@auth
def posts_list():
    ps = Post.query.order_by(Post.id.desc()).limit(50).all()
    liked = {x for (x,) in db.session.query(PostLike.post_id).filter(PostLike.user_id == g.user.id, PostLike.post_id.in_([p.id for p in ps] or [0]))}
    users = {u.id: u for u in User.query.filter(User.id.in_({p.user_id for p in ps} or [0])).all()}
    return jsonify(posts=[dict(id=p.id, uid=p.user_id, name=p.username, text=p.text, likes=p.likes, liked=p.id in liked, ts=p.ts,
                               photo=photo_url(users[p.user_id]) if p.user_id in users else '',
                               fr=frame_of(users[p.user_id]) if p.user_id in users else '',
                               vip=vip_active(users[p.user_id]) if p.user_id in users else False, mine=p.user_id == g.user.id) for p in ps])


@app.post('/api/posts/create')
@auth
def post_create():
    t = str(body().get('text', '')).strip()[:500]
    if len(t) < 2:
        return err('ጽሁፍ ያስገቡ')
    if limited('post', 10):
        return err('ቀስ በል', 429)
    db.session.add(Post(user_id=g.user.id, username=g.user.username, text=t))
    db.session.commit()
    mission_inc(g.user.id, 'post')
    return jsonify(ok=1)


@app.post('/api/posts/<int:pid>/like')
@auth
def post_like(pid):
    if not db.session.get(Post, pid):
        return err('not found', 404)
    ex = db.session.get(PostLike, (pid, g.user.id))
    if ex:
        db.session.delete(ex)
        d = -1
    else:
        db.session.add(PostLike(post_id=pid, user_id=g.user.id))
        d = 1
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify(ok=1)
    db.session.execute(update(Post).where(Post.id == pid).values(likes=Post.likes + d))
    db.session.commit()
    return jsonify(liked=d == 1)


@app.post('/api/posts/<int:pid>/delete')
@auth
def post_delete(pid):
    p = db.session.get(Post, pid)
    if p and (p.user_id == g.user.id or g.user.tg_id in ADMIN_IDS):
        PostLike.query.filter_by(post_id=pid).delete()
        db.session.delete(p)
        db.session.commit()
    return jsonify(ok=1)


@app.get('/api/jobs')
@auth
def jobs_list():
    js = Job.query.order_by(Job.id.desc()).limit(50).all()
    return jsonify(jobs=[dict(id=j.id, name=j.username, title=j.title, desc=j.desc, contact=j.contact, ts=j.ts, mine=j.user_id == g.user.id) for j in js])


@app.post('/api/jobs/create')
@auth
def job_create():
    b = body()
    t, d, c = str(b.get('title', '')).strip()[:80], str(b.get('desc', '')).strip()[:400], str(b.get('contact', '')).strip()[:80]
    if len(t) < 3 or not c:
        return err('ርዕስ እና ማግኛ ያስገቡ')
    if limited('job', 20):
        return err('ቀስ በል', 429)
    db.session.add(Job(user_id=g.user.id, username=g.user.username, title=t, desc=d, contact=c))
    db.session.commit()
    return jsonify(ok=1)


@app.post('/api/jobs/<int:jid>/delete')
@auth
def job_delete(jid):
    j = db.session.get(Job, jid)
    if j and (j.user_id == g.user.id or g.user.tg_id in ADMIN_IDS):
        db.session.delete(j)
        db.session.commit()
    return jsonify(ok=1)


# ----------------------------------------------------------------------------- games (server-side, atomic)
def pick_weighted(table):
    x, acc = rnd.random(), 0
    for m, w in table:
        acc += w
        if x < acc:
            return m
    return table[-1][0]


@app.get('/api/games')
@auth
def games_list():
    open_ = P2P.query.filter_by(status='open').order_by(P2P.id.desc()).limit(30).all()
    return jsonify(p2p=[dict(id=x.id, name=x.creator_name, bet=x.bet, mine=x.creator_id == g.user.id) for x in open_])


@app.post('/api/games/<name>')
@auth
def game_play(name):
    if name not in ('dice', 'flip', 'wheel', 'race', 'box'):
        return err('not found', 404)
    b = body()
    bet = num(b.get('bet'))
    pick = num(b.get('pick'), 1)
    if not MIN_BET <= bet <= MAX_BET:
        return err(f'ውርርድ {MIN_BET}–{MAX_BET}')
    if limited('game', 0.7):
        return err('ቀስ በል', 429)
    if not spend(g.user.id, bet):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    res, mult = {}, 0
    if name == 'dice':
        pick = min(6, max(1, pick))
        win = rnd.random() < P_DICE
        roll = pick if win else rnd.choice([n for n in range(1, 7) if n != pick])
        mult, res = (4.2 if win else 0), dict(win=win, roll=roll)
    elif name == 'flip':
        win = rnd.random() < P_FLIP
        side = pick if win else (2 if pick == 1 else 1)
        mult, res = (1.4 if win else 0), dict(win=win, side=side)
    elif name == 'race':
        pick = min(6, max(1, pick))
        win = rnd.random() < P_RACE
        winner = pick if win else rnd.choice([n for n in range(1, 7) if n != pick])
        order = [winner] + rnd.sample([n for n in range(1, 7) if n != winner], 5)
        mult, res = (4.5 if win else 0), dict(win=win, winner=winner, order=order)
    elif name == 'wheel':
        mult = pick_weighted(WHEEL)
        res = dict(mult=mult)
    else:
        mult = pick_weighted(BOX)
        res = dict(mult=mult, pick=pick)
    payout = int(bet * mult)
    if payout:
        add_coins(g.user.id, payout)
    tx(g.user.id, 'game', payout - bet, name)
    db.session.commit()
    mission_inc(g.user.id, 'game')
    res.update(payout=payout, bet=bet, coins=coins_of(g.user.id))
    return jsonify(res)


@app.post('/api/games/p2p/create')
@auth
def p2p_create():
    bet = num(body().get('bet'))
    if not MIN_BET <= bet <= MAX_BET:
        return err(f'ውርርድ {MIN_BET}–{MAX_BET}')
    if P2P.query.filter_by(creator_id=g.user.id, status='open').count() >= 2:
        return err('ክፍት ጦርነት አለህ')
    if not spend(g.user.id, bet):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    db.session.add(P2P(creator_id=g.user.id, creator_name=g.user.username, bet=bet))
    tx(g.user.id, 'p2p', -bet, 'create')
    db.session.commit()
    return jsonify(coins=coins_of(g.user.id))


@app.post('/api/games/p2p/cancel')
@auth
def p2p_cancel():
    gid = num(body().get('id'))
    r = db.session.execute(update(P2P).where(P2P.id == gid, P2P.creator_id == g.user.id, P2P.status == 'open').values(status='cancel'),
                           execution_options={'synchronize_session': False})
    if r.rowcount == 1:
        bet = db.session.get(P2P, gid).bet
        add_coins(g.user.id, bet)
        tx(g.user.id, 'p2p', bet, 'cancel')
    db.session.commit()
    return jsonify(coins=coins_of(g.user.id))


@app.post('/api/games/p2p/join')
@auth
def p2p_join():
    gid = num(body().get('id'))
    gm = db.session.get(P2P, gid)
    if not gm or gm.status != 'open' or gm.creator_id == g.user.id:
        return err('ጦርነቱ አይገኝም')
    if not spend(g.user.id, gm.bet):
        db.session.rollback()
        return err('ሳንቲም አይበቃም', 402)
    r = db.session.execute(update(P2P).where(P2P.id == gid, P2P.status == 'open').values(status='done'), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback()
        return err('ሌላ ሰው ቀድሟል')
    win_me = rnd.random() < 0.5
    prize = int(gm.bet * P2P_MULT)
    winner = g.user.id if win_me else gm.creator_id
    add_coins(winner, prize)
    tx(g.user.id, 'p2p', (prize - gm.bet) if win_me else -gm.bet, 'join')
    tx(gm.creator_id, 'p2p', 0 if win_me else prize - gm.bet, 'result')
    db.session.commit()
    if not win_me:
        notify(db.session.get(User, gm.creator_id).tg_id, f'🏆 P2P ጦርነት አሸንፈሃል! +{prize} coins')
    mission_inc(g.user.id, 'game')
    return jsonify(win=win_me, prize=prize, bet=gm.bet, coins=coins_of(g.user.id), vs=gm.creator_name)


# ----------------------------------------------------------------------------- admin
@app.get('/api/admin/overview')
@admin_only
def adm_overview():
    t = now()
    return jsonify(users=User.query.count(), banned=User.query.filter_by(banned=True).count(), rooms=Room.query.count(),
                   online=db.session.query(func.count(func.distinct(Presence.user_id))).filter(Presence.seen > t - 20).scalar() or 0,
                   vip=User.query.filter(User.vip_until > t).count(),
                   pending_buy=Purchase.query.filter_by(status='pending').count(),
                   pending_wd=Withdraw.query.filter_by(status='pending').count(),
                   coins_total=db.session.query(func.coalesce(func.sum(User.coins), 0)).scalar(),
                   earn_total=db.session.query(func.coalesce(func.sum(User.earnings), 0)).scalar(),
                   revenue=db.session.query(func.coalesce(func.sum(Purchase.birr), 0)).filter(Purchase.status == 'approved').scalar(),
                   paid=db.session.query(func.coalesce(func.sum(Withdraw.birr), 0)).filter(Withdraw.status == 'paid').scalar())


@app.get('/api/admin/purchases')
@admin_only
def adm_purchases():
    st = request.args.get('status', 'pending')
    ps = Purchase.query.filter_by(status=st).order_by(Purchase.id.desc()).limit(50).all()
    return jsonify(items=[dict(id=p.id, user_id=p.user_id, name=p.username, coins=p.coins, birr=p.birr, ref=p.ref, ts=p.ts) for p in ps])


@app.get('/api/admin/withdraws')
@admin_only
def adm_withdraws():
    st = request.args.get('status', 'pending')
    ws = Withdraw.query.filter_by(status=st).order_by(Withdraw.id.desc()).limit(50).all()
    return jsonify(items=[dict(id=w.id, user_id=w.user_id, name=w.username, coins=w.coins, birr=w.birr, phone=w.phone, ts=w.ts) for w in ws])


@app.get('/api/admin/users')
@admin_only
def adm_users():
    q = (request.args.get('q') or '').strip()
    qs = User.query
    if q:
        qs = qs.filter(or_(User.username.ilike(f'%{q}%'), User.tg_id == q))
    us = qs.order_by(User.id.desc()).limit(40).all()
    return jsonify(items=[dict(id=u.id, tg=u.tg_id, name=u.username, coins=u.coins, earn=u.earnings, banned=u.banned, vip=vip_active(u)) for u in us])


@app.get('/api/admin/shot/<int:pid>')
@admin_only
def adm_shot(pid):
    s = db.session.get(Shot, pid)
    if not s:
        return Response(status=404)
    return Response(s.data, mimetype=s.mime)


@app.post('/api/admin/purchase')
@admin_only
def adm_purchase():
    b = body()
    pid, act = num(b.get('id')), b.get('action')
    p = db.session.get(Purchase, pid)
    if not p:
        return err('not found', 404)
    new = 'approved' if act == 'approve' else 'rejected'
    r = db.session.execute(update(Purchase).where(Purchase.id == pid, Purchase.status == 'pending').values(status=new), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback()
        return err('ቀድሞ ተስተናግዷል')
    u = db.session.get(User, p.user_id)
    if act == 'approve':
        add_coins(p.user_id, p.coins)
        tx(p.user_id, 'buy', p.coins, f'purchase #{p.id}')
        if u and u.referrer_id:
            bonus = p.coins // 10
            add_coins(u.referrer_id, bonus)
            tx(u.referrer_id, 'referral', bonus, f'from {u.id}')
            ru = db.session.get(User, u.referrer_id)
            notify(ru.tg_id if ru else None, f'🎁 የጋበዝከው ሰው ገዛ — +{bonus} coins ተሰጥቶሃል')
    db.session.commit()
    if u:
        notify(u.tg_id, f'✅ {p.coins} coins ተጨምሯል!' if act == 'approve' else f'❌ የ{p.coins} coins ግዢህ ውድቅ ሆኗል። ትክክለኛ ስክሪንሾት ላክ።')
    return jsonify(ok=1)


@app.post('/api/admin/withdraw')
@admin_only
def adm_withdraw():
    b = body()
    wid, act = num(b.get('id')), b.get('action')
    w = db.session.get(Withdraw, wid)
    if not w:
        return err('not found', 404)
    new = 'paid' if act == 'paid' else 'rejected'
    r = db.session.execute(update(Withdraw).where(Withdraw.id == wid, Withdraw.status == 'pending').values(status=new), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback()
        return err('ቀድሞ ተስተናግዷል')
    u = db.session.get(User, w.user_id)
    if act != 'paid':
        db.session.execute(update(User).where(User.id == w.user_id).values(earnings=User.earnings + w.coins), execution_options={'synchronize_session': False})
        tx(w.user_id, 'refund', w.coins, f'withdraw #{w.id} rejected')
    db.session.commit()
    if u:
        notify(u.tg_id, f'💰 {w.birr} Birr ተልኳል!' if act == 'paid' else f'❌ ማውጣትህ ውድቅ ሆኗል፤ {w.coins} ተመልሷል።')
    return jsonify(ok=1)


@app.post('/api/admin/user')
@admin_only
def adm_user():
    b = body()
    uid, act = num(b.get('id')), b.get('action')
    u = db.session.get(User, uid)
    if not u:
        return err('not found', 404)
    if u.tg_id in ADMIN_IDS and act in ('ban', 'delete'):
        return err('admin ላይ አይቻልም')
    if act == 'ban':
        u.banned = True
    elif act == 'unban':
        u.banned = False
    elif act == 'grant':
        n = num(b.get('amount'))
        if not -1000000 <= n <= 1000000 or n == 0:
            return err('bad amount')
        add_coins(uid, n)
        tx(uid, 'grant', n, 'admin')
    elif act == 'vip':  # give VIP days (amount = days, 1..365)
        d = num(b.get('amount'))
        if not 1 <= d <= 365:
            return err('bad days')
        u.vip_until = max(u.vip_until or 0, now()) + d * 86400
        tx(uid, 'vip', 0, f'admin {d}d')
    elif act == 'delete':
        for M in (Seat, Presence, Post, Job, Photo, UserFrame):
            M.query.filter(M.user_id == uid).delete(synchronize_session=False)
        db.session.delete(u)
    else:
        return err('bad action')
    db.session.commit()
    return jsonify(ok=1)


@app.post('/api/admin/room')
@admin_only
def adm_room():
    delete_room(str(body().get('id')))
    return jsonify(ok=1)


@app.post('/api/admin/import')
@admin_only
def adm_import():
    """Import users from the OLD db.json (upload it as multipart field 'file')."""
    f = request.files.get('file')
    try:
        old = json.loads(f.read().decode('utf-8'))['users']
    except Exception:
        return err('db.json ትክክል አይደለም')
    made, refs = 0, []
    for key, v in old.items():
        tgid = str(key).replace('tg_', '')
        if not tgid or User.query.filter_by(tg_id=tgid).first():
            continue
        u = User(tg_id=tgid, username=str(v.get('username') or 'User')[:40], coins=num(v.get('coins')), earnings=num(v.get('earnings')),
                 bio=str(v.get('bio') or '')[:160], phone=str(v.get('phone') or '')[:20], banned=bool(v.get('banned')),
                 gifts_sent=num(v.get('total_gifts_sent')), gifts_recv=num(v.get('total_gifts_received')), spent=num(v.get('total_gifts_sent')),
                 tg_photo=v.get('photo') if str(v.get('photo') or '').startswith('http') else '')
        db.session.add(u)
        refs.append((tgid, str(v.get('referrer') or '').replace('tg_', '')))
        made += 1
    db.session.commit()
    for tgid, rf in refs:
        if rf:
            a, r = User.query.filter_by(tg_id=tgid).first(), User.query.filter_by(tg_id=rf).first()
            if a and r:
                a.referrer_id = r.id
    db.session.commit()
    return jsonify(imported=made)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)), debug=False, threaded=True)
