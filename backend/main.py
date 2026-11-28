# -*- coding: utf-8 -*-
"""Twenty One Party - PRO backend + PWA + Guest Login + Finance + Coin Seller."""
import os, json, hmac, hashlib, time, random, secrets, threading, urllib.request, urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, Response, g, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import update, func, event, or_, and_, case, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
import base64, struct, zlib


class RtcTokenBuilder:
    @staticmethod
    def buildTokenWithUid(app_id, app_cert, channel, uid, role, expire_ts):
        def u16(n): return struct.pack('<H', n)
        def u32(n): return struct.pack('<I', n)
        def pstr(b): return u16(len(b)) + b
        privs = {1: expire_ts}
        if role == 1: privs.update({2: expire_ts, 3: expire_ts, 4: expire_ts})
        uid_s = '' if not uid else str(uid)
        salt = random.SystemRandom().randint(1, 99999999)
        msg = u32(salt) + u32(int(time.time()) + 24 * 3600) + u16(len(privs)) + b''.join(u16(k) + u32(v) for k, v in sorted(privs.items()))
        sig = hmac.new(app_cert.encode(), app_id.encode() + channel.encode() + uid_s.encode() + msg, hashlib.sha256).digest()
        content = pstr(sig) + u32(zlib.crc32(channel.encode()) & 0xffffffff) + u32(zlib.crc32(uid_s.encode()) & 0xffffffff) + pstr(msg)
        return '006' + app_id + base64.b64encode(content).decode()


BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATES_DIR = next((d for d in (os.path.join(BASE, 'templates'), os.path.join(BASE, '..', 'templates')) if os.path.isdir(d)), os.path.join(BASE, 'templates'))
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
BOT_USERNAME = os.environ.get('BOT_USERNAME', '')
ADMIN_IDS = {x.strip() for x in os.environ.get('ADMIN_IDS', '6746329545').split(',') if x.strip()}
AGORA_APP_ID = os.environ.get('AGORA_APP_ID', '')
AGORA_APP_CERT = os.environ.get('AGORA_APP_CERT', '')
PAY_NUMBER = os.environ.get('TELEBIRR_NUMBER', '')
CHANNEL_URL = os.environ.get('CHANNEL_URL', 'https://t.me/TWENTYONEPARTYCOMMUNITY')
SUPPORT = os.environ.get('SUPPORT', '@TwentyOneOwner')
DEV = os.environ.get('DEV_MODE') == '1'
DB_URL = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(DATA_DIR, 'app.db')
if DB_URL.startswith('postgres://'): DB_URL = DB_URL.replace('postgres://', 'postgresql://', 1)
IS_SQLITE = DB_URL.startswith('sqlite')
SECRET = hashlib.sha256(('sess:' + (os.environ.get('SECRET_KEY') or BOT_TOKEN or 'dev-secret')).encode()).digest()

app = Flask(__name__)
# Trust forwarded client/protocol headers only when the deployment explicitly
# declares its reverse-proxy hop count. Default is zero (no proxy trust).
try:
    TRUSTED_PROXY_HOPS = max(0, min(5, int(os.environ.get('TRUSTED_PROXY_HOPS', '0'))))
except (TypeError, ValueError):
    TRUSTED_PROXY_HOPS = 0
if TRUSTED_PROXY_HOPS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXY_HOPS, x_proto=TRUSTED_PROXY_HOPS)
app.config.update(SQLALCHEMY_DATABASE_URI=DB_URL, SQLALCHEMY_TRACK_MODIFICATIONS=False, MAX_CONTENT_LENGTH=5 * 1024 * 1024, JSON_AS_ASCII=False,
                  SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True, 'pool_recycle': 300, 'pool_size': 10, 'max_overflow': 20} if not IS_SQLITE else {'pool_pre_ping': True})
app.json.ensure_ascii = False
db = SQLAlchemy(app)


@event.listens_for(Engine, 'connect')
def _sqlite_pragmas(con, rec):
    if IS_SQLITE:
        c = con.cursor(); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=15000'); c.close()


SEATS = 21
MAX_LIVE_GUESTS = 3
MIN_WITHDRAW = 500
COIN_BIRR = 0.4
USDT_ADDRESS = os.environ.get('USDT_ADDRESS', 'TUTGyjoRFaqviTtejUnXe92BcU8z3BaTUE')
USDT_BIRR = float(os.environ.get('USDT_BIRR', '161'))
COIN_USDT = round(COIN_BIRR / USDT_BIRR, 6)
WELCOME = 100
MIN_BET, MAX_BET = 10, 5000
LEVELS = [(0, 'Newbie', '🌱'), (500, 'Star', '⭐'), (2000, 'Pro', '💎'), (6000, 'Elite', '🔥'), (20000, 'Legend', '👑'), (60000, 'King', '🦁')]
PACKAGES = [(100, 50), (500, 240), (1000, 470), (2500, 1150), (5000, 2250), (10000, 4400), (25000, 10750), (50000, 21000)]
GIFTS = [
    ('rose', 'Rose', '🌹', 10, 'common'), ('heart', 'Heart', '💖', 20, 'common'), ('coffee', 'Coffee', '☕', 30, 'common'), ('cake', 'Cake', '🍰', 50, 'common'),
    ('kiss', 'Kiss', '💋', 80, 'rare'), ('teddy', 'Teddy', '🧸', 120, 'rare'), ('ring', 'Ring', '💍', 200, 'rare'),
    ('flower', 'Flower', '🌸', 300, 'epic'), ('crown', 'Crown', '👑', 500, 'epic'), ('guitar', 'Guitar', '🎸', 800, 'epic'),
    ('car', 'Car', '🏎️', 1200, 'luxury'), ('jet', 'Jet', '✈️', 2000, 'luxury'), ('yacht', 'Yacht', '🛥️', 3000, 'luxury'), ('castle', 'Castle', '🏰', 5000, 'luxury'),
    ('rocket', 'Rocket', '🚀', 8000, 'legendary'), ('dragon', 'Dragon', '🐉', 12000, 'legendary'), ('phoenix', 'Phoenix', '🔥', 20000, 'legendary'),
    ('galaxy', 'Galaxy', '🌌', 30000, 'mythic'), ('unicorn', 'Unicorn', '🦄', 40000, 'mythic'), ('universe', 'Universe', '🪐', 50000, 'mythic'),
    ('lucky1', 'Lucky Clover', '🍀', 10, 'lucky'), ('lucky2', 'Lucky Star', '⭐', 50, 'lucky'), ('lucky3', 'Lucky Gem', '💎', 200, 'lucky'), ('lucky4', 'Lucky Crown', '👑', 1000, 'lucky'),
]
GIFT_MAP = {k: dict(key=k, name=n, emoji=e, price=p, tier=t) for k, n, e, p, t in GIFTS}
HOST_SHARE = 0.50
INVITE_REWARD = 50
LUCKY_TABLE = [(0, .74), (1, .12), (2, .08), (5, .04), (10, .015), (14, .005)]
PK_WINNER_SHARE, PK_LOSER_SHARE, PK_PLATFORM_SHARE = 0.70, 0.20, 0.10
PK_DURATION = 300
SHARE = {'luxury': 0.50, 'legendary': 0.04, 'mythic': 0.04}   # never above HOST_SHARE: 50% + 12% keeps 38% for the platform
DEFAULT_SHARE = 0.02
WHEEL = [(0, .41), (0.5, .34), (1, .12), (2, .09), (5, .04)]
BOX = [(0, .58), (0.5, .20), (1, .09), (2, .07), (5, .05), (10, .01)]
P_DICE, P_FLIP, P_RACE = 0.7 / 4.2, 0.7 / 1.4, 0.7 / 4.5
P2P_MULT = 1.9
VIP_PRICE, VIP_DAYS = 1000, 30
VIP_DAILY_BONUS = 1.5
FREE_ROOMS, VIP_ROOMS = 3, 5
FRAMES = [('neon', 'Neon', 150, False), ('ice', 'Ice', 300, False), ('fire', 'Fire', 400, False), ('rose', 'Rose', 600, False), ('rainbow', 'Rainbow', 1500, False), ('vgold', 'Gold', 0, True), ('vaurora', 'Aurora', 0, True)]
FRAME_MAP = {k: dict(key=k, name=n, price=p, vip=v) for k, n, p, v in FRAMES}
MISSIONS = [('join', 'Join a room', '🎧', 1, 5), ('chat', 'Send 5 messages', '💬', 5, 10), ('seat', 'Take a seat', '🎤', 1, 10), ('gift', 'Send a gift', '🎁', 1, 15), ('game', 'Play 3 games', '🎮', 3, 10), ('post', 'Write a post', '📝', 1, 5)]
MISSION_MAP = {m[0]: m for m in MISSIONS}
MISSION_BONUS = 30
rnd = random.SystemRandom()


def now(): return int(time.time())


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True); tg_id = db.Column(db.String(32), unique=True, index=True); username = db.Column(db.String(60), default='User')
    coins = db.Column(db.Integer, default=0, nullable=False); earnings = db.Column(db.Integer, default=0, nullable=False); spent = db.Column(db.Integer, default=0, nullable=False)
    gifts_sent = db.Column(db.Integer, default=0, nullable=False); gifts_recv = db.Column(db.Integer, default=0, nullable=False)
    bio = db.Column(db.String(160), default=''); phone = db.Column(db.String(20), default=''); tg_photo = db.Column(db.String(300), default='')
    has_photo = db.Column(db.Boolean, default=False); photo_v = db.Column(db.Integer, default=0); banned = db.Column(db.Boolean, default=False)
    referrer_id = db.Column(db.Integer, nullable=True); joined = db.Column(db.Integer, default=now); last_daily = db.Column(db.String(10), default=''); streak = db.Column(db.Integer, default=0)
    vip_until = db.Column(db.Integer, default=0); frame = db.Column(db.String(20), default=''); real_coins = db.Column(db.Integer, default=0, nullable=False)
    host_status = db.Column(db.String(8), default='none'); is_agent = db.Column(db.Boolean, default=False); agent_id = db.Column(db.Integer, nullable=True)
    parent_agent_id = db.Column(db.Integer, nullable=True); commission = db.Column(db.Integer, default=0, nullable=False); auth_version = db.Column(db.Integer, default=0, nullable=False)


class Photo(db.Model):
    user_id = db.Column(db.Integer, primary_key=True); data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))


class UserFrame(db.Model):
    user_id = db.Column(db.Integer, primary_key=True); key = db.Column(db.String(20), primary_key=True); ts = db.Column(db.Integer, default=now)


class MissionProgress(db.Model):
    user_id = db.Column(db.Integer, primary_key=True); day = db.Column(db.String(10), primary_key=True); key = db.Column(db.String(12), primary_key=True)
    count = db.Column(db.Integer, default=0, nullable=False); claimed = db.Column(db.Boolean, default=False, nullable=False)


class Room(db.Model):
    id = db.Column(db.String(16), primary_key=True); name = db.Column(db.String(60)); topic = db.Column(db.String(100), default='')
    host_id = db.Column(db.Integer, index=True); kind = db.Column(db.String(8), default='voice'); created = db.Column(db.Integer, default=now)
    last_active = db.Column(db.Integer, default=now, index=True); version = db.Column(db.Integer, default=0, nullable=False)


class Seat(db.Model):
    id = db.Column(db.Integer, primary_key=True); room_id = db.Column(db.String(16), index=True); idx = db.Column(db.Integer); user_id = db.Column(db.Integer); muted = db.Column(db.Boolean, default=False)
    __table_args__ = (db.UniqueConstraint('room_id', 'idx'), db.UniqueConstraint('room_id', 'user_id'))


class LiveGuest(db.Model):
    id = db.Column(db.Integer, primary_key=True); room_id = db.Column(db.String(16), index=True); user_id = db.Column(db.Integer, index=True)
    idx = db.Column(db.Integer, default=1); status = db.Column(db.String(10), default='invited'); muted = db.Column(db.Boolean, default=False); ts = db.Column(db.Integer, default=now)
    __table_args__ = (db.UniqueConstraint('room_id', 'user_id'),)


class Presence(db.Model):
    room_id = db.Column(db.String(16), primary_key=True); user_id = db.Column(db.Integer, primary_key=True); seen = db.Column(db.Integer, index=True)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True); room_id = db.Column(db.String(16), index=True); user_id = db.Column(db.Integer); name = db.Column(db.String(60))
    text = db.Column(db.String(300), default=''); kind = db.Column(db.String(8), default='chat'); meta = db.Column(db.Text, default=''); ts = db.Column(db.Integer, default=now, index=True)


class Tx(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); kind = db.Column(db.String(20)); amount = db.Column(db.Integer); note = db.Column(db.String(120), default=''); ts = db.Column(db.Integer, default=now)


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); username = db.Column(db.String(60)); coins = db.Column(db.Integer); birr = db.Column(db.Integer)
    ref = db.Column(db.String(40), unique=True); status = db.Column(db.String(20), default='pending', index=True); ts = db.Column(db.Integer, default=now)
    method = db.Column(db.String(10), default='telebirr'); txhash = db.Column(db.String(80), default='')


class Shot(db.Model):
    purchase_id = db.Column(db.Integer, primary_key=True); data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))


class Withdraw(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); username = db.Column(db.String(60)); coins = db.Column(db.Integer); birr = db.Column(db.Integer)
    phone = db.Column(db.String(20)); status = db.Column(db.String(10), default='pending', index=True); ts = db.Column(db.Integer, default=now)
    method = db.Column(db.String(10), default='telebirr'); usdt_address = db.Column(db.String(64), default='')
    # Preserve the exact source buckets so a rejected withdrawal refunds correctly.
    earnings_coins = db.Column(db.Integer, default=0, nullable=False)
    commission_coins = db.Column(db.Integer, default=0, nullable=False)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); username = db.Column(db.String(60)); text = db.Column(db.String(500))
    likes = db.Column(db.Integer, default=0); ts = db.Column(db.Integer, default=now, index=True); has_image = db.Column(db.Boolean, default=False)


class PostImage(db.Model):
    post_id = db.Column(db.Integer, primary_key=True); data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))


class PostLike(db.Model):
    post_id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, primary_key=True)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); username = db.Column(db.String(60)); title = db.Column(db.String(80))
    desc = db.Column(db.String(400)); contact = db.Column(db.String(80)); ts = db.Column(db.Integer, default=now, index=True); has_image = db.Column(db.Boolean, default=False)


class JobImage(db.Model):
    job_id = db.Column(db.Integer, primary_key=True); data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))


class P2P(db.Model):
    id = db.Column(db.Integer, primary_key=True); creator_id = db.Column(db.Integer, index=True); creator_name = db.Column(db.String(60)); bet = db.Column(db.Integer)
    status = db.Column(db.String(8), default='open', index=True); ts = db.Column(db.Integer, default=now)


class PKBattle(db.Model):
    id = db.Column(db.Integer, primary_key=True); host1_id = db.Column(db.Integer, index=True); host2_id = db.Column(db.Integer, index=True, nullable=True)
    host1_gifts = db.Column(db.Integer, default=0); host2_gifts = db.Column(db.Integer, default=0); status = db.Column(db.String(10), default='active')
    host1_real = db.Column(db.Integer, default=0); host2_real = db.Column(db.Integer, default=0)
    started_at = db.Column(db.Integer, default=now); ended_at = db.Column(db.Integer, nullable=True); duration = db.Column(db.Integer, default=PK_DURATION); winner_id = db.Column(db.Integer, nullable=True)


class LuckyStats(db.Model):
    day = db.Column(db.String(10), primary_key=True); volume = db.Column(db.Integer, default=0); returned = db.Column(db.Integer, default=0); house = db.Column(db.Integer, default=0)


def ensure_columns():
    from sqlalchemy import inspect
    want = {User: [('vip_until', 'INTEGER DEFAULT 0'), ('frame', "VARCHAR(20) DEFAULT ''"), ('real_coins', 'INTEGER DEFAULT 0 NOT NULL'),
                   ('host_status', "VARCHAR(8) DEFAULT 'none'"), ('is_agent', 'BOOLEAN DEFAULT FALSE'), ('agent_id', 'INTEGER'), ('parent_agent_id', 'INTEGER'), ('commission', 'INTEGER DEFAULT 0 NOT NULL'), ('auth_version', 'INTEGER DEFAULT 0 NOT NULL')],
            Room: [('kind', "VARCHAR(8) DEFAULT 'voice'")], Post: [('has_image', 'BOOLEAN DEFAULT FALSE')], Job: [('has_image', 'BOOLEAN DEFAULT FALSE')],
            PKBattle: [('winner_id', 'INTEGER'), ('host1_real', 'INTEGER DEFAULT 0'), ('host2_real', 'INTEGER DEFAULT 0')],
            Purchase: [('method', "VARCHAR(10) DEFAULT 'telebirr'"), ('txhash', "VARCHAR(80) DEFAULT ''")],
            Withdraw: [('method', "VARCHAR(10) DEFAULT 'telebirr'"), ('usdt_address', "VARCHAR(64) DEFAULT ''"), ('earnings_coins', 'INTEGER DEFAULT 0 NOT NULL'), ('commission_coins', 'INTEGER DEFAULT 0 NOT NULL')]}
    insp = inspect(db.engine)
    for M, cols in want.items():
        tbl = M.__tablename__; have = {c['name'] for c in insp.get_columns(tbl)}
        for name, ddl in cols:
            if name in have: continue
            try:
                db.session.execute(sa_text(f'ALTER TABLE "{tbl}" ADD COLUMN {name} {ddl}')); db.session.commit()
            except Exception as e:
                db.session.rollback(); print('ensure_columns:', tbl, name, e)
    # Withdrawal workflow states include UNDER_REVIEW and PROCESSING (12+ chars).
    # PostgreSQL enforces VARCHAR length, so widen existing installations safely.
    if db.engine.dialect.name == 'postgresql':
        try:
            status_col = next((c for c in inspect(db.engine).get_columns(Withdraw.__tablename__)
                               if c['name'] == 'status'), None)
            if status_col and getattr(status_col['type'], 'length', None) and status_col['type'].length < 20:
                db.session.execute(sa_text('ALTER TABLE withdraw ALTER COLUMN status TYPE VARCHAR(20)'))
                db.session.commit()
        except Exception as e:
            db.session.rollback(); print('ensure_columns: withdraw status length', e)


_init_lock = threading.Lock()
with app.app_context():
    with _init_lock:
        try: db.create_all()
        except Exception as e: print('create_all:', e)
        try: ensure_columns()
        except Exception as e: print('ensure_columns:', e)


def err(m, c=400): return jsonify(error=m), c
def body(): return request.get_json(silent=True) or {}
def num(v, d=0):
    try: return int(v)
    except Exception: return d
def sign(uid, days=30):
    u = db.session.get(User, uid)
    version = int(u.auth_version or 0) if u else 0
    p = f'{uid}.{now() + days * 86400}.{version}'
    return p + '.' + hmac.new(SECRET, p.encode(), hashlib.sha256).hexdigest()[:32]
def parse_token(t):
    try:
        parts = t.split('.')
        if len(parts) == 3:  # accept existing sessions until their normal expiry
            uid, exp, sig = parts; version = 0; payload = f'{uid}.{exp}'
        elif len(parts) == 4:
            uid, exp, version, sig = parts; payload = f'{uid}.{exp}.{version}'
        else: return None
        ok = hmac.compare_digest(sig, hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32])
        if not ok or int(exp) <= now(): return None
        return int(uid), int(version)
    except Exception: return None
def verify_init(init):
    # Reject duplicate fields: silently keeping the last copy can make the
    # signed payload ambiguous to different parsers.
    pairs = parse_qsl(init, keep_blank_values=True)
    data = dict(pairs)
    if len(data) != len(pairs): return None
    h = data.pop('hash', None)
    if not h or not BOT_TOKEN: return None
    check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
    key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h): return None
    auth_date = num(data.get('auth_date'), -1)
    age = now() - auth_date
    # Telegram init data is short-lived. Reject missing, stale, and
    # implausibly future-dated payloads instead of accepting them for 3 days.
    if auth_date <= 0 or age > 86400 or age < -60: return None
    try: return json.loads(data['user']), data
    except Exception: return None
def auth(f):
    @wraps(f)
    def w(*a, **k):
        h = request.headers.get('Authorization', ''); claims = parse_token(h[7:]) if h.startswith('Bearer ') else None
        uid, version = claims if claims else (None, None)
        u = db.session.get(User, uid) if uid else None
        if not u or int(u.auth_version or 0) != version: return err('unauthorized', 401)
        if u.banned: return err('Your account is banned', 403)
        g.user = u; return f(*a, **k)
    return w
def admin_only(f):
    @wraps(f)
    @auth
    def w(*a, **k):
        if g.user.tg_id not in ADMIN_IDS: return err('forbidden', 403)
        return f(*a, **k)
    return w
_rl = {}
def limited(key, gap):
    t = time.time(); k = f'{g.user.id}:{key}'
    if t - _rl.get(k, 0) < gap: return True
    _rl[k] = t
    if len(_rl) > 60000: _rl.clear()
    return False
def level_of(spent):
    i = 0
    for n, (t, _, _) in enumerate(LEVELS):
        if spent >= t: i = n
    return i
def photo_url(u): return f'/api/photo/{u.id}?v={u.photo_v}' if u.has_photo else (u.tg_photo or '')
def vip_active(u): return (u.vip_until or 0) > now()
def frame_of(u):
    f = FRAME_MAP.get(u.frame or ''); return f['key'] if f and (not f['vip'] or vip_active(u)) else ''
def pub(u):
    lv = level_of(u.spent)
    return dict(id=u.id, name=u.username, photo=photo_url(u), lv=lv, lvn=LEVELS[lv][1], lvi=LEVELS[lv][2], bio=u.bio or '', vip=vip_active(u), fr=frame_of(u), host=u.host_status or 'none', ag=bool(u.is_agent))
def me_dict(u):
    d = pub(u); nxt = LEVELS[d['lv'] + 1][0] if d['lv'] + 1 < len(LEVELS) else None
    d.update(coins=u.coins, earnings=u.earnings, spent=u.spent, phone=u.phone or '', tg_id=u.tg_id, is_admin=u.tg_id in ADMIN_IDS, sent=u.gifts_sent, recv=u.gifts_recv,
             next=nxt, base=LEVELS[d['lv']][0], streak=u.streak, daily_ready=u.last_daily != today(), vip_until=u.vip_until or 0, comm=u.commission or 0)
    return d
def today(): return (datetime.utcnow() + timedelta(hours=3)).strftime('%Y-%m-%d')
def coins_of(uid): return db.session.query(User.coins).filter(User.id == uid).scalar() or 0
def spend(uid, n):
    r = db.session.execute(update(User).where(User.id == uid, User.coins >= n).values(coins=User.coins - n, real_coins=case((User.real_coins > User.coins - n, User.coins - n), else_=User.real_coins)), execution_options={'synchronize_session': False})
    return r.rowcount == 1
def spend_split(uid, n):
    """Spends n coins (free coins first). Returns how many PURCHASED coins were used, or None.
    The row is locked so parallel requests cannot make free coins count as purchased."""
    row = db.session.query(User.coins, User.real_coins).filter(User.id == uid).with_for_update().first()
    if not row or not spend(uid, n): return None
    return max(0, n - max(0, (row[0] or 0) - (row[1] or 0)))
def add_coins(uid, n): db.session.execute(update(User).where(User.id == uid).values(coins=User.coins + n), execution_options={'synchronize_session': False})
def tx(uid, kind, amt, note=''): db.session.add(Tx(user_id=uid, kind=kind, amount=amt, note=note[:120]))
def notify(chat_id, text):
    if not BOT_TOKEN or not chat_id: return
    def run():
        try:
            data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
            urllib.request.urlopen(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data, timeout=8)
        except Exception: pass
    threading.Thread(target=run, daemon=True).start()
def notify_admins(t):
    for a in ADMIN_IDS: notify(a, t)
def sniff_image(b):
    if b[:3] == b'\xff\xd8\xff': return 'image/jpeg'
    if b[:8] == b'\x89PNG\r\n\x1a\n': return 'image/png'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP': return 'image/webp'
    return None
@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options'] = 'nosniff'; r.headers['Referrer-Policy'] = 'no-referrer'; r.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.path.startswith('/api/') and 'Cache-Control' not in r.headers: r.headers['Cache-Control'] = 'no-store'
    return r
@app.errorhandler(413)
def too_big(e): return err('File too large', 413)


_TPL = next((p for p in (os.path.join(TEMPLATES_DIR, 'index.html'), os.path.join(BASE, '..', 'templates', 'index.html')) if os.path.exists(p)), os.path.join(TEMPLATES_DIR, 'index.html'))
_HTML = open(_TPL, encoding='utf-8').read()
@app.get('/')
def index(): return Response(_HTML, mimetype='text/html', headers={'Cache-Control': 'no-cache'})
@app.get('/healthz')
def healthz():
    db.session.execute(sa_text('select 1')); return 'ok'


@app.get('/manifest.json')
def manifest():
    try:
        return Response(open(os.path.join(TEMPLATES_DIR, 'manifest.json'), encoding='utf-8').read(), mimetype='application/manifest+json', headers={'Cache-Control': 'no-cache'})
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get('/sw.js')
def sw():
    try:
        return Response(open(os.path.join(TEMPLATES_DIR, 'sw.js'), encoding='utf-8').read(), mimetype='application/javascript', headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get('/static/<path:fn>')
def staticf(fn):
    return send_from_directory(os.path.join(BASE, 'static'), fn)


try:
    from . import rules
except ImportError:
    import rules

try:
    from .finance import init_finance
except ImportError:
    from finance import init_finance

FIN = init_finance(app, db, dict(
    User=User, Tx=Tx, Purchase=Purchase, Withdraw=Withdraw, LuckyStats=LuckyStats, PKBattle=PKBattle,
    now=now, add_coins=add_coins, tx=tx, coin_birr=lambda: COIN_BIRR, usdt_birr=lambda: USDT_BIRR,
    is_admin=lambda u: u.tg_id in ADMIN_IDS, err=err, body=body, num=num, auth=auth, admin_only=admin_only,
    jsonify=jsonify, request=request))

try:
    from .seller import init_seller
except ImportError:
    from seller import init_seller

SELLER = init_seller(app, db, dict(
    User=User, Tx=Tx, now=now, tx=tx, err=err, body=body, num=num,
    auth=auth, admin_only=admin_only, notify=notify, notify_admins=notify_admins,
    sniff_image=sniff_image, coin_birr=lambda: COIN_BIRR))

GIFTS_PUB = [GIFT_MAP[k] for k, *_ in GIFTS]
FRAMES_PUB = list(FRAME_MAP.values())
def cfg():
    return dict(agora_app_id=AGORA_APP_ID, gifts=GIFTS_PUB, packages=[dict(coins=c, birr=b) for c, b in PACKAGES], levels=LEVELS, pay_number=PAY_NUMBER, bot=BOT_USERNAME,
                min_withdraw=MIN_WITHDRAW, rate=COIN_BIRR, seats=SEATS, max_guests=MAX_LIVE_GUESTS, min_bet=MIN_BET, max_bet=MAX_BET, p2p_mult=P2P_MULT, frames=FRAMES_PUB,
                channel=CHANNEL_URL, support=SUPPORT,
                host=dict(share=HOST_SHARE), agent=dict(rates=[float(r) for r in rules.GENERATION_RATES], depth=len(rules.GENERATION_RATES)), invite=dict(reward=INVITE_REWARD),
                vip=dict(price=VIP_PRICE, days=VIP_DAYS, bonus=VIP_DAILY_BONUS, rooms=VIP_ROOMS),
                lucky=dict(table=[[m, p] for m, p in LUCKY_TABLE], rtp=0.70, house=0.30),
                pk=dict(winner_share=PK_WINNER_SHARE, loser_share=PK_LOSER_SHARE, platform_share=PK_PLATFORM_SHARE, duration=PK_DURATION),
                usdt=dict(address=USDT_ADDRESS, network='TRC20', birr_per_usdt=USDT_BIRR, coin_usdt=COIN_USDT),
                seller=dict(tiers=[SELLER['TIER_MAP'][t[0]] for t in SELLER['TIERS']], retail=0.50))


try:
    from .phone_login import init_auth, client_ip
except ImportError:
    from phone_login import init_auth, client_ip

AUTH = init_auth(app, db, dict(
    User=User, now=now, tx=tx, err=err, body=body, num=num, auth=auth, admin_only=admin_only,
    sign=sign, me_dict=me_dict, cfg=cfg, welcome=WELCOME,
    referral_register=lambda a, b: FIN['referral_register'](a, b), is_admin=lambda u: u.tg_id in ADMIN_IDS,
    bot_token=BOT_TOKEN, secret=lambda: SECRET))
# Never sign sessions with a public default key (anyone could forge an admin login).
if not (os.environ.get('SECRET_KEY') or BOT_TOKEN):
    SECRET = AUTH['stored_secret']()


@app.post('/api/register')
def register():
    b = body(); init = b.get('init') or ''; start = ''
    if init:
        v = verify_init(init)
        if not v: return err('invalid telegram data', 401)
        tu, data = v; tgid = str(tu['id'])
        name = ((tu.get('first_name') or '') + ' ' + (tu.get('last_name') or '')).strip() or tu.get('username') or 'User'
        photo = tu.get('photo_url') or ''; start = data.get('start_param') or ''
    elif b.get('guest'):
        gid = str(b.get('guest'))[:40]
        tgid = 'g_' + hashlib.sha256(gid.encode()).hexdigest()[:16]
        name = 'Guest'
        photo = ''
        if not User.query.filter_by(tg_id=tgid).first() and not AUTH['hit'](AUTH['hkey']('gu', client_ip()), 15, 3600):
            return err('Too many guest accounts. Try again later', 429)
    elif DEV:
        tgid = str(b.get('dev_id') or '1'); name = 'Dev ' + tgid; photo = ''
    else:
        return err('Please log in with your phone number', 401)
    u = User.query.filter_by(tg_id=tgid).first()
    if not u and tgid.startswith('g_') and AUTH['verify_on']():
        return err('Guest mode is closed. Please sign up with your phone number', 403)
    if not u:
        ref = None; ag = None
        if start.startswith('ref_'):
            r = User.query.filter_by(tg_id=start[4:]).first(); ref = r.id if r else None
        elif start.startswith('ag_'):
            a = User.query.filter_by(tg_id=start[3:]).first(); ag = a.id if a and a.is_agent else None
        bonus = 0 if tgid.startswith('g_') else WELCOME  # no free coins for guests (stops coin farming)
        u = User(tg_id=tgid, username=name[:40], coins=bonus, tg_photo=photo, referrer_id=ref, agent_id=ag)
        db.session.add(u)
        try:
            db.session.flush(); tx(u.id, 'welcome', bonus, 'Welcome bonus')
            if ref: FIN['referral_register'](ref, u.id)
            if ag: FIN['referral_register'](ag, u.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); u = User.query.filter_by(tg_id=tgid).first()
    elif photo and u.tg_photo != photo:
        u.tg_photo = photo; db.session.commit()
    if u.banned: return err('Your account is banned', 403)
    return jsonify(token=sign(u.id), me=me_dict(u), cfg=cfg())
@app.get('/api/me')
@auth
def me(): return jsonify(me=me_dict(g.user), cfg=cfg())
@app.post('/api/profile')
@auth
def profile():
    b = body(); u = g.user
    if 'name' in b:
        n = str(b['name']).strip()[:30]
        if len(n) < 2: return err('Name too short')
        u.username = n
    if 'bio' in b: u.bio = str(b['bio']).strip()[:160]
    if 'phone' in b:
        p = str(b['phone']).strip()
        if p and not (p.isdigit() and len(p) == 10 and p[:2] in ('09', '07')): return err('Phone must be 09xxxxxxxx')
        u.phone = p
    db.session.commit(); return jsonify(me=me_dict(u))
@app.post('/api/profile/photo')
@auth
def profile_photo():
    f = request.files.get('file'); data = f.read() if f else b''; mime = sniff_image(data)
    if not mime or len(data) > 1024 * 1024: return err('Image under 1MB')
    p = db.session.get(Photo, g.user.id)
    if p: p.data, p.mime = data, mime
    else: db.session.add(Photo(user_id=g.user.id, data=data, mime=mime))
    g.user.has_photo = True; g.user.photo_v = (g.user.photo_v or 0) + 1; db.session.commit(); return jsonify(me=me_dict(g.user))
@app.get('/api/photo/<int:uid>')
def photo(uid):
    p = db.session.get(Photo, uid)
    if not p: return Response(status=404)
    return Response(p.data, mimetype=p.mime, headers={'Cache-Control': 'public, max-age=86400'})
@app.post('/api/daily')
@auth
def daily():
    u = g.user; t = today()
    if u.last_daily == t: return err('Already claimed today')
    y = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=1)).strftime('%Y-%m-%d')
    streak = (u.streak or 0) + 1 if u.last_daily == y else 1
    reward = 20 + 5 * min(streak - 1, 8)
    if vip_active(u): reward = int(reward * VIP_DAILY_BONUS)
    if not FIN['reward_allowed'](t, 'daily', reward): return err('Daily reward budget reached')
    r = db.session.execute(update(User).where(User.id == u.id, User.last_daily != t).values(last_daily=t, streak=streak, coins=User.coins + reward), execution_options={'synchronize_session': False})
    if r.rowcount != 1: return err('Already claimed')
    tx(u.id, 'daily', reward, f'Day {streak}'); db.session.commit()
    FIN['reward_spend'](t, 'daily', reward); FIN['ledger'](u.id, 'daily', reward); db.session.commit()
    db.session.refresh(u)
    return jsonify(reward=reward, streak=streak, me=me_dict(u))
_lb = {'t': 0, 'd': None}
@app.get('/api/leaderboard')
@auth
def leaderboard():
    if now() - _lb['t'] > 30 or not _lb['d']:
        ts = User.query.filter(User.gifts_sent > 0, User.banned == False).order_by(User.gifts_sent.desc()).limit(10).all()
        tr = User.query.filter(User.gifts_recv > 0, User.banned == False).order_by(User.gifts_recv.desc()).limit(10).all()
        _lb['d'] = dict(senders=[dict(pub(u), v=u.gifts_sent) for u in ts], receivers=[dict(pub(u), v=u.gifts_recv) for u in tr]); _lb['t'] = now()
    return jsonify(_lb['d'])


_last_clean = {}; _last_gc = {'t': 0}
def bump(rid):
    db.session.execute(update(Room).where(Room.id == rid).values(version=Room.version + 1), execution_options={'synchronize_session': False}); db.session.commit()
def get_room(rid): return db.session.get(Room, rid)
def touch(rid, uid):
    t = now(); p = db.session.get(Presence, (rid, uid)); previous_seen = p.seen if p else None
    try:
        if not p: db.session.add(Presence(room_id=rid, user_id=uid, seen=t)); db.session.commit()
        elif t - p.seen >= 8: p.seen = t; db.session.commit()
    except IntegrityError: db.session.rollback()
    try:
        if 'HOST_REWARDS' in globals(): HOST_REWARDS['heartbeat'](rid, uid, previous_seen, t)
    except Exception as e:
        db.session.rollback(); print('host activity heartbeat:', e)
def online_count(rid): return db.session.query(func.count()).select_from(Presence).filter(Presence.room_id == rid, Presence.seen > now() - 20).scalar() or 0
def guests_payload(rid):
    rows = db.session.query(LiveGuest, User).join(User, User.id == LiveGuest.user_id).filter(LiveGuest.room_id == rid, LiveGuest.status == 'live').order_by(LiveGuest.idx).all()
    return [dict(idx=gv.idx, u=pub(u), muted=bool(gv.muted)) for gv, u in rows]
def clean_room(rid):
    t = now()
    if t - _last_clean.get(rid, 0) < 10: return
    _last_clean[rid] = t
    if len(_last_clean) > 5000: _last_clean.clear()
    stale = [x for (x,) in db.session.query(Presence.user_id).filter(Presence.room_id == rid, Presence.seen < t - 30)]
    if stale:
        n = Seat.query.filter(Seat.room_id == rid, Seat.user_id.in_(stale)).delete(synchronize_session=False)
        Presence.query.filter(Presence.room_id == rid, Presence.user_id.in_(stale)).delete(synchronize_session=False); db.session.commit()
        if n: bump(rid)
    changed = False
    for gv in LiveGuest.query.filter_by(room_id=rid).all():
        p = db.session.get(Presence, (rid, gv.user_id))
        if gv.status == 'live' and (not p or p.seen < t - 60): db.session.delete(gv); changed = True
        elif gv.status == 'invited' and t - gv.ts > 180: db.session.delete(gv); changed = True
    if changed: db.session.commit(); bump(rid)
    room = db.session.get(Room, rid)
    if room and room.kind == 'live' and t - room.created > 30 and not Seat.query.filter_by(room_id=rid, idx=0).first():
        delete_room(rid); return
    Message.query.filter(Message.room_id == rid, Message.ts < t - 7200).delete(synchronize_session=False)
    if online_count(rid) > 0: db.session.execute(update(Room).where(Room.id == rid).values(last_active=t))
    db.session.commit()
def seats_payload(rid):
    rows = db.session.query(Seat, User).join(User, User.id == Seat.user_id).filter(Seat.room_id == rid).all()
    return [dict(i=s.idx, u=pub(u), m=bool(s.muted)) for s, u in rows]
def gc_rooms():
    t = now()
    if t - _last_gc['t'] < 120: return
    _last_gc['t'] = t
    for (rid,) in db.session.query(Room.id).filter(Room.last_active < t - 3 * 86400).all(): delete_room(rid)
def delete_room(rid):
    for M in (Seat, Presence, Message, LiveGuest): M.query.filter(M.room_id == rid).delete(synchronize_session=False)
    Room.query.filter(Room.id == rid).delete(synchronize_session=False); db.session.commit()
@app.get('/api/rooms')
@auth
def rooms():
    gc_rooms(); t = now()
    rs = Room.query.filter(or_(and_(Room.kind == 'live', Room.last_active > t - 90), and_(or_(Room.kind == 'voice', Room.kind.is_(None)), Room.last_active > t - 6 * 3600))).order_by(Room.last_active.desc()).limit(100).all()
    on = dict(db.session.query(Presence.room_id, func.count()).filter(Presence.seen > t - 20).group_by(Presence.room_id).all())
    st = dict(db.session.query(Seat.room_id, func.count()).group_by(Seat.room_id).all())
    gu = dict(db.session.query(LiveGuest.room_id, func.count()).filter(LiveGuest.status == 'live').group_by(LiveGuest.room_id).all())
    hosts = {u.id: u for u in User.query.filter(User.id.in_([r.host_id for r in rs] or [0])).all()}
    out = [dict(id=r.id, kind=r.kind or 'voice', name=r.name, topic=r.topic, online=on.get(r.id, 0), seated=st.get(r.id, 0), guests=gu.get(r.id, 0), host=pub(hosts[r.host_id]) if r.host_id in hosts else None) for r in rs]
    out.sort(key=lambda x: -x['online']); return jsonify(rooms=out)
@app.post('/api/rooms/create')
@auth
def room_create():
    b = body(); name = str(b.get('name', '')).strip()[:40]
    if len(name) < 2: return err('Enter room name')
    kind = 'live' if b.get('kind') == 'live' else 'voice'
    if kind == 'live':
        for (old,) in db.session.query(Room.id).filter(Room.host_id == g.user.id, Room.kind == 'live').all(): delete_room(old)
    elif Room.query.filter(Room.host_id == g.user.id, Room.kind != 'live').count() >= (VIP_ROOMS if vip_active(g.user) else FREE_ROOMS):
        return err('Max rooms reached')
    r = Room(id=secrets.token_hex(4), name=name, topic=str(b.get('topic', '')).strip()[:100], host_id=g.user.id, kind=kind)
    db.session.add(r); db.session.commit(); return jsonify(id=r.id)
@app.post('/api/rooms/<rid>/join')
@auth
def room_join(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    uid = g.user.id
    if 'FEED' in globals() and r.host_id in FEED['hidden_user_ids'](uid):
        return err('ROOM_GONE', 404)
    Seat.query.filter(Seat.user_id == uid, Seat.room_id != rid).delete(synchronize_session=False)
    Presence.query.filter(Presence.user_id == uid, Presence.room_id != rid).delete(synchronize_session=False)
    LiveGuest.query.filter(LiveGuest.user_id == uid, LiveGuest.room_id != rid, LiveGuest.status == 'live').delete(synchronize_session=False)
    db.session.commit()
    touch(rid, uid)
    if uid == r.host_id and not Seat.query.filter_by(room_id=rid, user_id=uid).first():
        try:
            Seat.query.filter_by(room_id=rid, idx=0).delete(); db.session.add(Seat(room_id=rid, idx=0, user_id=uid)); db.session.commit(); bump(rid)
        except IntegrityError: db.session.rollback()
    db.session.execute(update(Room).where(Room.id == rid).values(last_active=now())); db.session.commit(); mission_inc(uid, 'join')
    return jsonify(room=dict(id=r.id, kind=r.kind or 'voice', name=r.name, topic=r.topic, host_id=r.host_id))
@app.get('/api/rooms/<rid>/state')
@auth
def room_state(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    if 'FEED' in globals() and r.host_id in FEED['hidden_user_ids'](g.user.id):
        return err('ROOM_GONE', 404)
    touch(rid, g.user.id); clean_room(rid)
    r = db.session.get(Room, rid)
    if not r: return err('ROOM_GONE', 404)
    cv, mid = num(request.args.get('v'), -1), num(request.args.get('mid'))
    out = dict(v=r.version, online=online_count(rid), guests=guests_payload(rid))
    if cv != r.version: out['seats'] = seats_payload(rid)
    q = Message.query.filter(Message.room_id == rid)
    hidden = FEED['hidden_user_ids'](g.user.id) if 'FEED' in globals() else set()
    if hidden: q = q.filter(~Message.user_id.in_(hidden))
    ms = list(reversed(q.order_by(Message.id.desc()).limit(30).all())) if mid <= 0 else q.filter(Message.id > mid).order_by(Message.id).limit(60).all()
    vips = {i for (i,) in db.session.query(User.id).filter(User.id.in_({m.user_id for m in ms} or {0}), User.vip_until > now())} if ms else set()
    out['msgs'] = [dict(id=m.id, u=m.user_id, n=m.name, t=m.text, k=m.kind, v=m.user_id in vips, meta=json.loads(m.meta) if m.meta else None) for m in ms]
    return jsonify(out)
@app.get('/api/rooms/<rid>/audience')
@auth
def room_audience(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    touch(rid, g.user.id); clean_room(rid)
    t = now()
    hidden = FEED['hidden_user_ids'](g.user.id) if 'FEED' in globals() else set()
    q = db.session.query(Presence, User).join(User, User.id == Presence.user_id).filter(
        Presence.room_id == rid, Presence.seen > t - 20
    )
    if hidden: q = q.filter(~User.id.in_(hidden))
    q = q.order_by(Presence.seen.desc())
    total = q.count()
    rows = q.limit(100).all()
    seat_ids = {x.user_id for x in Seat.query.filter_by(room_id=rid).all()}
    guest_ids = {x.user_id for x in LiveGuest.query.filter_by(room_id=rid, status='live').all()}
    users = []
    for presence, user in rows:
        role = 'Host' if user.id == r.host_id else 'Guest' if user.id in guest_ids else 'Speaker' if user.id in seat_ids else 'Listener'
        users.append(dict(pub(user), role=role, seen=presence.seen))
    return jsonify(users=users, total=total, truncated=total > len(users))
@app.post('/api/rooms/<rid>/seat')
@auth
def take_seat(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    idx = num(body().get('idx'), -1)
    if not 0 <= idx < SEATS: return err('bad seat')
    if (idx == 0) != (g.user.id == r.host_id): return err('Seat 0 host only')
    try:
        Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete(); db.session.add(Seat(room_id=rid, idx=idx, user_id=g.user.id)); db.session.commit()
    except IntegrityError:
        db.session.rollback(); return err('Seat taken', 409)
    touch(rid, g.user.id); bump(rid)
    if idx != 0: mission_inc(g.user.id, 'seat')
    return jsonify(ok=1)
@app.post('/api/rooms/<rid>/leave-seat')
@auth
def leave_seat(rid):
    n = Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete(); db.session.commit()
    if n: bump(rid)
    return jsonify(ok=1)
@app.post('/api/rooms/<rid>/exit')
@auth
def room_exit(rid):
    r = get_room(rid)
    if r and r.kind == 'live' and r.host_id == g.user.id: delete_room(rid); return jsonify(ok=1)
    Seat.query.filter_by(room_id=rid, user_id=g.user.id).delete()
    Presence.query.filter_by(room_id=rid, user_id=g.user.id).delete()
    LiveGuest.query.filter_by(room_id=rid, user_id=g.user.id).delete(); db.session.commit(); bump(rid)
    return jsonify(ok=1)
@app.post('/api/rooms/<rid>/seat-action')
@auth
def seat_action(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    if g.user.id != r.host_id and g.user.tg_id not in ADMIN_IDS: return err('Host only', 403)
    b = body(); uid, act = num(b.get('uid')), b.get('action')
    if uid == r.host_id: return err('Cannot act on host')
    s = Seat.query.filter_by(room_id=rid, user_id=uid).first()
    if not s: return err('User not on seat')
    if act == 'kick': db.session.delete(s)
    elif act in ('mute', 'unmute'): s.muted = act == 'mute'
    else: return err('bad action')
    db.session.commit(); bump(rid); return jsonify(ok=1)
@app.get('/api/rooms/<rid>/invite-candidates')
@auth
def invite_candidates(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    t = now()
    inroom = {x.user_id for x in Presence.query.filter_by(room_id=rid).all()} | {r.host_id}
    taken = {x.user_id for x in LiveGuest.query.filter_by(room_id=rid).all()}
    seen = {x for (x,) in db.session.query(Presence.user_id).filter(Presence.seen > t - 300).all()}
    pool = User.query.filter(User.banned == False, User.id != g.user.id).order_by(User.id.desc()).limit(80).all()
    out = []
    for u in pool:
        if u.id in inroom or u.id in taken: continue
        out.append(dict(pub(u), online=u.id in seen))
        if len(out) >= 24: break
    out.sort(key=lambda x: (not x['online'], x['name'].lower()))
    return jsonify(users=out, slots=max(0, MAX_LIVE_GUESTS - LiveGuest.query.filter_by(room_id=rid).count()))
@app.post('/api/rooms/<rid>/invite')
@auth
def live_invite(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    if r.host_id != g.user.id and g.user.tg_id not in ADMIN_IDS: return err('Host only', 403)
    if (r.kind or 'voice') != 'live': return err('Guests are for LIVE rooms')
    uid = num(body().get('uid'))
    if uid == g.user.id: return err('Cannot invite yourself')
    t = db.session.get(User, uid)
    if not t: return err('User not found')
    if t.banned: return err('User is banned')
    ex = LiveGuest.query.filter_by(room_id=rid, user_id=uid).first()
    if ex:
        if ex.status == 'live': return err('Already on stage')
        db.session.delete(ex); db.session.commit()
    if LiveGuest.query.filter_by(room_id=rid).count() >= MAX_LIVE_GUESTS: return err('Stage is full')
    used = {x.idx for x in LiveGuest.query.filter_by(room_id=rid).all()}
    idx = next((i for i in range(1, MAX_LIVE_GUESTS + 1) if i not in used), 1)
    db.session.add(LiveGuest(room_id=rid, user_id=uid, idx=idx, status='invited'))
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback(); return err('Already invited')
    feed_notify = globals().get('FEED', {}).get('notify_user')
    if feed_notify: feed_notify(uid, g.user.id, 'room_invite', 'Live stage invitation', f'{g.user.username} invited you to join {r.name}', f'room:{rid}')
    notify(t.tg_id, f'🎥 {g.user.username} invited you on stage in "{r.name}". Open the app to join!')
    bump(rid); return jsonify(ok=1)
@app.get('/api/my-invites')
@auth
def my_invites():
    rows = db.session.query(LiveGuest, Room, User).join(Room, Room.id == LiveGuest.room_id).join(User, User.id == Room.host_id).filter(
        LiveGuest.user_id == g.user.id, LiveGuest.status == 'invited').order_by(LiveGuest.id.desc()).limit(3).all()
    return jsonify(invites=[dict(room_id=lv.room_id, room=rm.name, host=pub(h)) for lv, rm, h in rows])
@app.post('/api/rooms/<rid>/invite/respond')
@auth
def invite_respond(rid):
    gv = LiveGuest.query.filter_by(room_id=rid, user_id=g.user.id, status='invited').first()
    if not gv: return err('No invite', 404)
    if body().get('action') == 'accept':
        gv.status = 'live'; gv.ts = now(); db.session.commit(); bump(rid); return jsonify(ok=1, accepted=1)
    db.session.delete(gv); db.session.commit(); bump(rid); return jsonify(ok=1, accepted=0)
@app.post('/api/rooms/<rid>/guest/leave')
@auth
def guest_leave(rid):
    n = LiveGuest.query.filter_by(room_id=rid, user_id=g.user.id).delete(); db.session.commit()
    if n: bump(rid)
    return jsonify(ok=1)
@app.post('/api/rooms/<rid>/guest-action')
@auth
def guest_action(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    if r.host_id != g.user.id and g.user.tg_id not in ADMIN_IDS: return err('Host only', 403)
    b = body(); uid = num(b.get('uid')); act = b.get('action')
    gv = LiveGuest.query.filter_by(room_id=rid, user_id=uid).first()
    if not gv: return err('Not on stage')
    if act == 'kick': db.session.delete(gv)
    elif act in ('mute', 'unmute'): gv.muted = act == 'mute'
    else: return err('bad action')
    db.session.commit(); bump(rid); return jsonify(ok=1)
@app.post('/api/rooms/<rid>/chat')
@auth
def room_chat(rid):
    room = get_room(rid)
    if not room: return err('ROOM_GONE', 404)
    if 'FEED' in globals() and room.host_id in FEED['hidden_user_ids'](g.user.id):
        return err('ROOM_GONE', 404)
    t = str(body().get('text', '')).strip()[:200]
    if not t: return err('empty')
    if limited('chat', 0.8): return err('Slow down', 429)
    db.session.add(Message(room_id=rid, user_id=g.user.id, name=g.user.username, text=t)); db.session.commit(); mission_inc(g.user.id, 'chat'); return jsonify(ok=1)
@app.post('/api/rooms/<rid>/gift')
@auth
def room_gift(rid):
    r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    b = body(); gift = GIFT_MAP.get(b.get('gift')); qty = max(1, min(99, num(b.get('qty'), 1))); to = num(b.get('to'))
    if not gift or to == g.user.id: return err('bad request')
    on_stage = to == r.host_id or Seat.query.filter_by(room_id=rid, user_id=to).first() or LiveGuest.query.filter_by(room_id=rid, user_id=to, status='live').first()
    if not on_stage: return err('Recipient must be on stage')
    target = db.session.get(User, to)
    if not target: return err('user not found')
    if 'FEED' in globals() and to in FEED['hidden_user_ids'](g.user.id):
        return err('This interaction is unavailable', 403)
    if limited('gift', 0.4): return err('Slow down', 429)
    total = gift['price'] * qty
    bought_used = spend_split(g.user.id, total)
    if bought_used is None:
        db.session.rollback(); return err('Not enough coins', 402)
    lucky_m, refund = 0, 0; earn = 0; real_used = 0
    if gift['tier'] == 'lucky':
        lucky_m = pick_weighted(LUCKY_TABLE); refund = lucky_m * total
        if refund: add_coins(g.user.id, refund); tx(g.user.id, 'lucky_win', refund, f'x{lucky_m} {gift["key"]}')
        tx(g.user.id, 'lucky', -total, gift['key'])
        db.session.execute(update(User).where(User.id == to).values(gifts_recv=User.gifts_recv + total), execution_options={'synchronize_session': False})
        st = db.session.get(LuckyStats, today())
        if not st: st = LuckyStats(day=today()); db.session.add(st)
        st.volume = (st.volume or 0) + total; st.returned = (st.returned or 0) + refund; st.house = (st.house or 0) + (total - refund)
    else:
        real_used = bought_used
        rate = HOST_SHARE if target.host_status == 'host' else SHARE.get(gift['tier'], DEFAULT_SHARE)
        earn = int(real_used * rate)
        db.session.execute(update(User).where(User.id == to).values(earnings=User.earnings + earn, gifts_recv=User.gifts_recv + total), execution_options={'synchronize_session': False})
        if earn: tx(to, 'earn', earn, f'gift from {g.user.id}')
        tx(g.user.id, 'gift', -total, f'{gift["key"]}x{qty} -> {to}')
    db.session.execute(update(User).where(User.id == g.user.id).values(spent=User.spent + total, gifts_sent=User.gifts_sent + total), execution_options={'synchronize_session': False})
    gid, pool = FIN['record_gift'](g.user.id, to, gift['key'], qty, total, real_used, earn, rid, gift['tier'],
                                   f"{rid}:{g.user.id}:{to}:{gift['key']}:{qty}:{time.time()}:{secrets.token_hex(4)}")
    if gift['tier'] != 'lucky' and gid:
        FIN['pay_commissions'](to, real_used, g.user.id, gid)
    meta = dict(gift=gift['key'], emoji=gift['emoji'], qty=qty, tier=gift['tier'], to=to, to_name=target.username, price=total, lucky=lucky_m, refund=refund)
    db.session.add(Message(room_id=rid, user_id=g.user.id, name=g.user.username, kind='gift', meta=json.dumps(meta, ensure_ascii=False)))
    db.session.commit(); mission_inc(g.user.id, 'gift')
    FIN['wallet_sync'](g.user.id); FIN['wallet_sync'](to)
    return jsonify(coins=coins_of(g.user.id), lucky=lucky_m, refund=refund)
@app.post('/api/agora-token')
@auth
def agora_token():
    rid = body().get('room_id'); r = get_room(rid)
    if not r: return err('ROOM_GONE', 404)
    if not AGORA_APP_ID: return err('Agora not configured', 500)
    seated = bool(Seat.query.filter_by(room_id=rid, user_id=g.user.id).first())
    guest = bool(LiveGuest.query.filter_by(room_id=rid, user_id=g.user.id, status='live').first())
    publisher = seated or guest or ((r.kind or 'voice') == 'live' and r.host_id == g.user.id)
    channel = 'room_' + rid; token = None
    if AGORA_APP_CERT: token = RtcTokenBuilder.buildTokenWithUid(AGORA_APP_ID, AGORA_APP_CERT, channel, g.user.id, 1 if publisher else 2, now() + 3600)
    return jsonify(app_id=AGORA_APP_ID, channel=channel, token=token, uid=g.user.id, publisher=publisher, guest=guest)


@app.post('/api/host/apply')
@auth
def host_apply():
    u = g.user
    if u.host_status == 'host': return err('Already a host')
    if u.host_status == 'pending': return err('Request pending')
    u.host_status = 'pending'; db.session.commit(); notify_admins(f'🎤 Host application: {u.username}'); return jsonify(ok=1)
@app.post('/api/agent/apply')
@auth
def agent_apply():
    u = g.user
    if u.is_agent: return err('Already an agent')
    if u.host_status != 'host': return err('Become a host first')
    u.is_agent = True
    if u.agent_id: u.parent_agent_id = u.agent_id
    db.session.commit(); notify_admins(f'🤝 New agent: {u.username}'); return jsonify(ok=1)
@app.get('/api/agent/dashboard')
@auth
def agent_dashboard():
    u = g.user
    if not u.is_agent: return err('Not an agent', 403)
    hosts = User.query.filter_by(agent_id=u.id).order_by(User.earnings.desc()).limit(50).all()
    subs = User.query.filter_by(parent_agent_id=u.id, is_agent=True).limit(50).all()
    total = db.session.query(func.coalesce(func.sum(Tx.amount), 0)).filter(Tx.user_id == u.id, Tx.kind == 'commission').scalar()
    link = web_link(f'ag_{u.tg_id}')
    return jsonify(comm=u.commission or 0, total=total, hosts=[dict(pub(h), earn=h.earnings, sent=h.gifts_sent) for h in hosts], subs=[dict(pub(s), comm=s.commission or 0) for s in subs], link=link)
def web_link(code):
    base = (os.environ.get('PUBLIC_URL') or os.environ.get('RENDER_EXTERNAL_URL') or f'https://{request.host}').rstrip('/')
    return f'{base}/?ref={code}'
@app.get('/api/invite')
@auth
def invite_info():
    u = g.user
    code = f'{"ag" if u.is_agent else "ref"}_{u.tg_id}'
    link = web_link(code)
    cnt = db.session.query(func.count()).select_from(User).filter(or_(User.agent_id == u.id, User.referrer_id == u.id)).scalar() or 0
    return jsonify(link=link, count=cnt, reward=INVITE_REWARD)


def seats_changed(uid):
    for (rid,) in db.session.query(Seat.room_id).filter(Seat.user_id == uid).all(): bump(rid)
def shop_state(u):
    owned = [k for (k,) in db.session.query(UserFrame.key).filter(UserFrame.user_id == u.id)]
    return dict(me=me_dict(u), owned=owned)
@app.get('/api/shop')
@auth
def shop(): return jsonify(shop_state(g.user))
@app.post('/api/vip/buy')
@auth
def vip_buy():
    u = g.user
    if limited('shop', 1.5): return err('Slow down', 429)
    if not spend(u.id, VIP_PRICE):
        db.session.rollback(); return err('Not enough coins', 402)
    t = now()
    db.session.execute(update(User).where(User.id == u.id).values(vip_until=case((User.vip_until > t, User.vip_until + VIP_DAYS * 86400), else_=t + VIP_DAYS * 86400), spent=User.spent + VIP_PRICE), execution_options={'synchronize_session': False})
    tx(u.id, 'vip', -VIP_PRICE, f'VIP {VIP_DAYS}d'); db.session.commit(); db.session.refresh(u); seats_changed(u.id); return jsonify(shop_state(u))
@app.post('/api/frame/buy')
@auth
def frame_buy():
    u = g.user; f = FRAME_MAP.get(str(body().get('key') or ''))
    if not f: return err('bad request')
    if f['vip']: return err('VIP only frame')
    if db.session.get(UserFrame, (u.id, f['key'])): return err('Already owned')
    if limited('shop', 1.5): return err('Slow down', 429)
    if not spend(u.id, f['price']):
        db.session.rollback(); return err('Not enough coins', 402)
    db.session.add(UserFrame(user_id=u.id, key=f['key']))
    db.session.execute(update(User).where(User.id == u.id).values(frame=f['key'], spent=User.spent + f['price']), execution_options={'synchronize_session': False})
    tx(u.id, 'frame', -f['price'], f['key'])
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback(); return err('Already owned')
    db.session.refresh(u); seats_changed(u.id); return jsonify(shop_state(u))
@app.post('/api/frame/equip')
@auth
def frame_equip():
    u = g.user; key = str(body().get('key') or '')
    if key:
        f = FRAME_MAP.get(key)
        if not f: return err('bad request')
        if f['vip']:
            if not vip_active(u): return err('VIP only frame')
        elif not db.session.get(UserFrame, (u.id, key)): return err('Buy first')
    if limited('equip', 0.5): return err('Slow down', 429)
    u.frame = key; db.session.commit(); seats_changed(u.id); return jsonify(shop_state(u))


_mgc = {'t': 0}
def mission_reward(u, base): return int(base * VIP_DAILY_BONUS) if vip_active(u) else base
def mission_inc(uid, key, n=1):
    if key not in MISSION_MAP: return
    d = today()
    def bump_row():
        r = db.session.execute(update(MissionProgress).where(MissionProgress.user_id == uid, MissionProgress.day == d, MissionProgress.key == key).values(count=MissionProgress.count + n), execution_options={'synchronize_session': False})
        return r.rowcount
    try:
        if bump_row() == 0: db.session.add(MissionProgress(user_id=uid, day=d, key=key, count=n, claimed=False))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        try: bump_row(); db.session.commit()
        except Exception: db.session.rollback()
    except Exception: db.session.rollback()
def missions_state(u):
    rows = {m.key: m for m in MissionProgress.query.filter_by(user_id=u.id, day=today()).all()}
    items = [dict(key=k, name=n, emoji=e, goal=gq, reward=mission_reward(u, rw), count=min(rows[k].count if k in rows else 0, gq), claimed=bool(k in rows and rows[k].claimed)) for k, n, e, gq, rw in MISSIONS]
    bonus = rows.get('all')
    return dict(items=items, coins=coins_of(u.id), bonus=dict(reward=mission_reward(u, MISSION_BONUS), ready=all(i['claimed'] for i in items) and not bonus, claimed=bool(bonus)))
@app.get('/api/missions')
@auth
def missions():
    t = now()
    if t - _mgc['t'] > 6 * 3600:
        _mgc['t'] = t; cutoff = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=3)).strftime('%Y-%m-%d')
        MissionProgress.query.filter(MissionProgress.day < cutoff).delete(synchronize_session=False); db.session.commit()
    return jsonify(missions_state(g.user))
@app.post('/api/missions/claim')
@auth
def mission_claim():
    u = g.user; key, d = str(body().get('key') or ''), today()
    if limited('mclaim', 0.5): return err('Slow down', 429)
    if key == 'all':
        b = missions_state(u)['bonus']
        if not b['ready']: return err('Finish all missions first')
        reward = b['reward']; db.session.add(MissionProgress(user_id=u.id, day=d, key='all', count=1, claimed=True))
        try: db.session.flush()
        except IntegrityError:
            db.session.rollback(); return err('Already claimed')
    else:
        m = MISSION_MAP.get(key)
        if not m: return err('bad request')
        reward = mission_reward(u, m[4])
        r = db.session.execute(update(MissionProgress).where(MissionProgress.user_id == u.id, MissionProgress.day == d, MissionProgress.key == key, MissionProgress.claimed == False, MissionProgress.count >= m[3]).values(claimed=True), execution_options={'synchronize_session': False})
        if r.rowcount != 1:
            db.session.rollback(); return err('Not ready or claimed')
    add_coins(u.id, reward); tx(u.id, 'mission', reward, key); db.session.commit()
    out = missions_state(u); out['got'] = reward; return jsonify(out)


@app.get('/api/wallet')
@auth
def wallet():
    u = g.user
    P = Purchase.query.filter_by(user_id=u.id).order_by(Purchase.id.desc()).limit(15).all()
    W = Withdraw.query.filter_by(user_id=u.id).order_by(Withdraw.id.desc()).limit(15).all()
    T = Tx.query.filter_by(user_id=u.id).order_by(Tx.id.desc()).limit(30).all()
    return jsonify(me=me_dict(u), purchases=[dict(id=p.id, coins=p.coins, birr=p.birr, status=p.status, ts=p.ts, method=p.method) for p in P],
                   withdraws=[dict(id=w.id, coins=w.coins, birr=w.birr, status=w.status, ts=w.ts, method=w.method) for w in W],
                   txs=[dict(kind=t.kind, amount=t.amount, note=t.note, ts=t.ts) for t in T])
@app.post('/api/coins/buy')
@auth
def coins_buy():
    pkg = num(request.form.get('coins')); ref_in = (request.form.get('ref') or '').strip()
    method = (request.form.get('method') or 'telebirr').strip()
    if method not in ('telebirr', 'usdt'): method = 'telebirr'
    price = dict(PACKAGES).get(pkg)
    if not price: return err('Choose a package')
    if method == 'usdt':
        if len(ref_in) < 10: return err('Enter USDT TX hash')
        ref = 'U' + secrets.token_hex(12); txhash = ref_in[:80]
    else:
        if len(ref_in) < 6: return err('Enter Telebirr transaction ID')
        ref = ref_in.upper()[:40]; txhash = ''
    f = request.files.get('file'); data = f.read() if f else b''; mime = sniff_image(data)
    if not mime: return err('Attach payment screenshot')
    if Purchase.query.filter_by(user_id=g.user.id, status='pending').count() >= 3: return err('3 pending requests')
    p = Purchase(user_id=g.user.id, username=g.user.username, coins=pkg, birr=price, ref=ref, method=method, txhash=txhash); db.session.add(p)
    try:
        db.session.flush(); db.session.add(Shot(purchase_id=p.id, data=data, mime=mime)); db.session.commit()
    except IntegrityError:
        db.session.rollback(); return err('Transaction ID already used')
    notify_admins(f'🛒 {method.upper()} Purchase: {g.user.username} — {pkg} coins / {price} Birr / {round(price/USDT_BIRR,2)} USDT\nRef: {txhash or ref}'); return jsonify(ok=1)
@app.post('/api/withdraw')
@auth
def withdraw():
    b = body(); coins = num(b.get('coins')); method = (b.get('method') or 'telebirr').strip()
    if method not in ('telebirr', 'usdt'): method = 'telebirr'
    if coins < MIN_WITHDRAW: return err(f'Minimum withdrawal {MIN_WITHDRAW} coins')
    phone = ''; address = ''
    if method == 'usdt':
        address = str(b.get('address') or '').strip()
        if not (address.startswith('T') and len(address) == 34): return err('Enter a valid TRC20 address (T…, 34 chars)')
    else:
        phone = str(b.get('phone') or g.user.phone or '').strip()
        if not (phone.isdigit() and len(phone) == 10): return err('Enter valid phone number')
    # Lock and split the debit between the two withdrawable source buckets.
    # These exact amounts are stored on the request and restored to the same
    # buckets if an administrator rejects it.
    balances = db.session.query(User.earnings, User.commission).filter(User.id == g.user.id).with_for_update().first()
    if not balances or int(balances[0] or 0) + int(balances[1] or 0) < coins:
        db.session.rollback(); return err('Not enough income')
    commission_part = min(max(0, int(balances[1] or 0)), coins)
    earnings_part = coins - commission_part
    r = db.session.execute(update(User).where(User.id == g.user.id, User.earnings >= earnings_part, User.commission >= commission_part).values(
        commission=User.commission - commission_part, earnings=User.earnings - earnings_part), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); return err('Balances changed; please retry')
    w = Withdraw(user_id=g.user.id, username=g.user.username, coins=coins, birr=int(coins * COIN_BIRR), phone=phone, method=method, usdt_address=address,
                 earnings_coins=earnings_part, commission_coins=commission_part); db.session.add(w)
    tx(g.user.id, 'withdraw', -coins, f'{method} withdraw'); db.session.commit()
    dest = address if method == 'usdt' else phone
    notify_admins(f'💸 {method.upper()} Withdraw: {g.user.username} — {coins} coins = {w.birr} Birr / {round(coins*COIN_USDT,4)} USDT → {dest}'); return jsonify(ok=1)


@app.get('/api/posts')
@auth
def posts_list():
    ps = Post.query.order_by(Post.id.desc()).limit(50).all()
    liked = {x for (x,) in db.session.query(PostLike.post_id).filter(PostLike.user_id == g.user.id, PostLike.post_id.in_([p.id for p in ps] or [0]))}
    users = {u.id: u for u in User.query.filter(User.id.in_({p.user_id for p in ps} or [0])).all()}
    out = []
    for p in ps:
        hi = bool(db.session.get(PostImage, p.id))
        out.append(dict(id=p.id, uid=p.user_id, name=p.username, text=p.text, likes=p.likes, liked=p.id in liked, ts=p.ts,
                        photo=photo_url(users[p.user_id]) if p.user_id in users else '', fr=frame_of(users[p.user_id]) if p.user_id in users else '',
                        vip=vip_active(users[p.user_id]) if p.user_id in users else False, mine=p.user_id == g.user.id, has_image=hi, image_url=f'/api/post-image/{p.id}' if hi else ''))
    return jsonify(posts=out)
@app.get('/api/post-image/<int:pid>')
def post_image(pid):
    img = db.session.get(PostImage, pid)
    if not img: return Response(status=404)
    return Response(img.data, mimetype=img.mime, headers={'Cache-Control': 'public, max-age=3600'})
@app.post('/api/posts/create')
@auth
def post_create():
    text = str(body().get('text', '')).strip()[:500]
    if len(text) < 2: return err('Enter text')
    if limited('post', 10): return err('Slow down', 429)
    p = Post(user_id=g.user.id, username=g.user.username, text=text); db.session.add(p)
    f = request.files.get('photo')
    if f:
        data = f.read(); mime = sniff_image(data)
        if mime and len(data) <= 2 * 1024 * 1024:
            try:
                db.session.flush(); db.session.add(PostImage(post_id=p.id, data=data, mime=mime)); p.has_image = True
            except Exception: pass
    db.session.commit(); mission_inc(g.user.id, 'post'); return jsonify(ok=1, post_id=p.id)
@app.post('/api/posts/<int:pid>/like')
@auth
def post_like(pid):
    if not db.session.get(Post, pid): return err('not found', 404)
    ex = db.session.get(PostLike, (pid, g.user.id))
    if ex: db.session.delete(ex); d = -1
    else: db.session.add(PostLike(post_id=pid, user_id=g.user.id)); d = 1
    try: db.session.flush()
    except IntegrityError:
        db.session.rollback(); return jsonify(ok=1)
    db.session.execute(update(Post).where(Post.id == pid).values(likes=Post.likes + d)); db.session.commit(); return jsonify(liked=d == 1)
@app.post('/api/posts/<int:pid>/delete')
@auth
def post_delete(pid):
    p = db.session.get(Post, pid)
    if p and (p.user_id == g.user.id or g.user.tg_id in ADMIN_IDS):
        PostLike.query.filter_by(post_id=pid).delete(); db.session.delete(p); db.session.commit()
    return jsonify(ok=1)
@app.get('/api/jobs')
@auth
def jobs_list():
    js = Job.query.order_by(Job.id.desc()).limit(50).all(); out = []
    for j in js:
        hi = bool(db.session.get(JobImage, j.id))
        out.append(dict(id=j.id, name=j.username, title=j.title, desc=j.desc, contact=j.contact, ts=j.ts, mine=j.user_id == g.user.id, has_image=hi, image_url=f'/api/job-image/{j.id}' if hi else ''))
    return jsonify(jobs=out)
@app.get('/api/job-image/<int:jid>')
def job_image(jid):
    img = db.session.get(JobImage, jid)
    if not img: return Response(status=404)
    return Response(img.data, mimetype=img.mime, headers={'Cache-Control': 'public, max-age=3600'})
@app.post('/api/jobs/create')
@auth
def job_create():
    b = body(); title = str(b.get('title', '')).strip()[:80]; desc = str(b.get('desc', '')).strip()[:400]; contact = str(b.get('contact', '')).strip()[:80]
    if len(title) < 3 or not contact: return err('Enter title and contact')
    if limited('job', 20): return err('Slow down', 429)
    j = Job(user_id=g.user.id, username=g.user.username, title=title, desc=desc, contact=contact); db.session.add(j)
    f = request.files.get('photo')
    if f:
        data = f.read(); mime = sniff_image(data)
        if mime and len(data) <= 2 * 1024 * 1024:
            try:
                db.session.flush(); db.session.add(JobImage(job_id=j.id, data=data, mime=mime)); j.has_image = True
            except Exception: pass
    db.session.commit(); return jsonify(ok=1, job_id=j.id)
@app.post('/api/jobs/<int:jid>/delete')
@auth
def job_delete(jid):
    j = db.session.get(Job, jid)
    if j and (j.user_id == g.user.id or g.user.tg_id in ADMIN_IDS): db.session.delete(j); db.session.commit()
    return jsonify(ok=1)


def pick_weighted(table):
    total = sum(w for _, w in table); x = rnd.random() * total; acc = 0.0
    for m, w in table:
        acc += w
        if x < acc: return m
    return table[0][0]
@app.get('/api/games')
@auth
def games_list():
    op = P2P.query.filter_by(status='open').order_by(P2P.id.desc()).limit(30).all()
    return jsonify(p2p=[dict(id=x.id, name=x.creator_name, bet=x.bet, mine=x.creator_id == g.user.id) for x in op])
@app.post('/api/games/<name>')
@auth
def game_play(name):
    if name not in ('dice', 'flip', 'wheel', 'race', 'box'): return err('not found', 404)
    b = body(); bet = num(b.get('bet')); pick = num(b.get('pick'), 1)
    if not MIN_BET <= bet <= MAX_BET: return err(f'Bet {MIN_BET}–{MAX_BET}')
    if limited('game', 0.7): return err('Slow down', 429)
    if not spend(g.user.id, bet):
        db.session.rollback(); return err('Not enough coins', 402)
    res, mult = {}, 0
    if name == 'dice':
        pick = min(6, max(1, pick)); win = rnd.random() < P_DICE
        roll = pick if win else rnd.choice([n for n in range(1, 7) if n != pick]); mult, res = (4.2 if win else 0), dict(win=win, roll=roll)
    elif name == 'flip':
        win = rnd.random() < P_FLIP; side = pick if win else (2 if pick == 1 else 1); mult, res = (1.4 if win else 0), dict(win=win, side=side)
    elif name == 'race':
        pick = min(6, max(1, pick)); win = rnd.random() < P_RACE
        winner = pick if win else rnd.choice([n for n in range(1, 7) if n != pick]); order = [winner] + rnd.sample([n for n in range(1, 7) if n != winner], 5)
        mult, res = (4.5 if win else 0), dict(win=win, winner=winner, order=order)
    elif name == 'wheel': mult = pick_weighted(WHEEL); res = dict(mult=mult)
    else: mult = pick_weighted(BOX); res = dict(mult=mult, pick=pick)
    payout = int(bet * mult)
    if payout: add_coins(g.user.id, payout)
    tx(g.user.id, 'game', payout - bet, name); db.session.commit(); mission_inc(g.user.id, 'game')
    res.update(payout=payout, bet=bet, coins=coins_of(g.user.id)); return jsonify(res)
@app.post('/api/games/p2p/create')
@auth
def p2p_create():
    bet = num(body().get('bet'))
    if not MIN_BET <= bet <= MAX_BET: return err(f'Bet {MIN_BET}–{MAX_BET}')
    if P2P.query.filter_by(creator_id=g.user.id, status='open').count() >= 2: return err('You have an open battle')
    if not spend(g.user.id, bet):
        db.session.rollback(); return err('Not enough coins', 402)
    db.session.add(P2P(creator_id=g.user.id, creator_name=g.user.username, bet=bet)); tx(g.user.id, 'p2p', -bet, 'create'); db.session.commit()
    return jsonify(coins=coins_of(g.user.id))
@app.post('/api/games/p2p/cancel')
@auth
def p2p_cancel():
    gid = num(body().get('id'))
    r = db.session.execute(update(P2P).where(P2P.id == gid, P2P.creator_id == g.user.id, P2P.status == 'open').values(status='cancel'), execution_options={'synchronize_session': False})
    if r.rowcount == 1:
        bet = db.session.get(P2P, gid).bet; add_coins(g.user.id, bet); tx(g.user.id, 'p2p', bet, 'cancel')
    db.session.commit(); return jsonify(coins=coins_of(g.user.id))
@app.post('/api/games/p2p/join')
@auth
def p2p_join():
    gid = num(body().get('id')); gm = db.session.get(P2P, gid)
    if not gm or gm.status != 'open' or gm.creator_id == g.user.id: return err('Battle not found')
    if not spend(g.user.id, gm.bet):
        db.session.rollback(); return err('Not enough coins', 402)
    r = db.session.execute(update(P2P).where(P2P.id == gid, P2P.status == 'open').values(status='done'), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); return err('Someone joined first')
    win_me = rnd.random() < 0.5; prize = int(gm.bet * P2P_MULT); winner = g.user.id if win_me else gm.creator_id
    add_coins(winner, prize); tx(g.user.id, 'p2p', (prize - gm.bet) if win_me else -gm.bet, 'join'); tx(gm.creator_id, 'p2p', 0 if win_me else prize - gm.bet, 'result')
    db.session.commit()
    if not win_me: notify(db.session.get(User, gm.creator_id).tg_id, f'🏆 P2P win! +{prize} coins')
    mission_inc(g.user.id, 'game'); return jsonify(win=win_me, prize=prize, bet=gm.bet, coins=coins_of(g.user.id), vs=gm.creator_name)


def finalize_pk(pk):
    # atomic claim: many viewers poll at once, only one request may pay out
    r = db.session.execute(update(PKBattle).where(PKBattle.id == pk.id, PKBattle.status == 'active').values(status='ended', ended_at=now()), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); db.session.refresh(pk); return
    db.session.refresh(pk)
    h1, h2 = pk.host1_gifts or 0, pk.host2_gifts or 0
    if h1 + h2 > 0:
        real = (pk.host1_real or 0) + (pk.host2_real or 0)
        if h1 == h2 and pk.host2_id:      # draw: 45% / 45% / 10% as written in the policy
            each = int(real * (PK_WINNER_SHARE + PK_LOSER_SHARE) / 2)
            pay = [(pk.host1_id, each, 'pk_draw'), (pk.host2_id, each, 'pk_draw')]
        else:
            win, lose = (pk.host1_id, pk.host2_id) if h1 >= h2 else (pk.host2_id, pk.host1_id)
            pk.winner_id = win
            pay = [(win, int(real * PK_WINNER_SHARE), 'pk_win'), (lose, int(real * PK_LOSER_SHARE), 'pk_lose')]
        for uid, amount, kind in pay:
            if uid and amount:
                db.session.execute(update(User).where(User.id == uid).values(earnings=User.earnings + amount)); tx(uid, kind, amount, f'PK #{pk.id}')
    db.session.commit()
    feed_notify = globals().get('FEED', {}).get('notify_user')
    if feed_notify and pk.host2_id:
        for uid in (pk.host1_id, pk.host2_id):
            if pk.winner_id == uid: result = 'You won the PK battle'
            elif pk.winner_id is None: result = 'Your PK battle ended in a draw or without a winner'
            else: result = 'Your PK battle has ended'
            feed_notify(uid, None, 'pk_result', 'PK battle result', result, f'pk:{pk.id}')
@app.post('/api/pk/create')
@auth
def pk_create():
    u = g.user
    if u.host_status != 'host': return err('Only hosts can create PK', 403)
    if PKBattle.query.filter(or_(PKBattle.host1_id == u.id, PKBattle.host2_id == u.id), PKBattle.status == 'active').first(): return err('Already in a PK', 409)
    pk = PKBattle(host1_id=u.id, duration=PK_DURATION); db.session.add(pk); db.session.commit()
    return jsonify(pk_id=pk.id)
@app.post('/api/pk/join')
@auth
def pk_join():
    u = g.user
    if u.host_status != 'host': return err('Only hosts can join PK', 403)
    pk = db.session.get(PKBattle, num(body().get('pk_id')))
    if not pk or pk.status != 'active': return err('PK not found', 404)
    if pk.host1_id == u.id: return err('Cannot challenge yourself')
    if pk.host2_id: return err('PK full', 409)
    if PKBattle.query.filter(or_(PKBattle.host1_id == u.id, PKBattle.host2_id == u.id), PKBattle.status == 'active').first(): return err('Already in a PK', 409)
    pk.host2_id = u.id; pk.started_at = now(); db.session.commit(); return jsonify(ok=1, pk_id=pk.id)
@app.post('/api/pk/gift')
@auth
def pk_gift():
    u = g.user; pk = db.session.get(PKBattle, num(body().get('pk_id')))
    if not pk or pk.status != 'active': return err('PK not found', 404)
    to_host = num(body().get('to_host')); gift = GIFT_MAP.get(body().get('gift')); qty = max(1, min(99, num(body().get('qty'), 1)))
    if not gift: return err('Gift not found')
    total = gift['price'] * qty
    if to_host == 1 and pk.host1_id: score, paid = 'host1_gifts', 'host1_real'
    elif to_host == 2 and pk.host2_id: score, paid = 'host2_gifts', 'host2_real'
    else: return err('Invalid host')
    if now() > pk.started_at + pk.duration:
        finalize_pk(pk); return err('PK has ended', 409)
    real_used = spend_split(u.id, total)
    if real_used is None:
        db.session.rollback(); return err('Not enough coins', 402)
    r = db.session.execute(update(PKBattle).where(PKBattle.id == pk.id, PKBattle.status == 'active').values(
        {score: func.coalesce(getattr(PKBattle, score), 0) + total, paid: func.coalesce(getattr(PKBattle, paid), 0) + real_used}), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); return err('PK has ended', 409)
    db.session.execute(update(User).where(User.id == u.id).values(spent=User.spent + total, gifts_sent=User.gifts_sent + total), execution_options={'synchronize_session': False})
    tx(u.id, 'pk_gift', -total, f'PK#{pk.id} side{to_host} {gift["key"]} {qty}'); db.session.commit(); db.session.refresh(pk)
    return jsonify(coins=coins_of(u.id), host1_gifts=pk.host1_gifts, host2_gifts=pk.host2_gifts)
@app.post('/api/pk/end')
@auth
def pk_end():
    pk = db.session.get(PKBattle, num(body().get('pk_id')))
    if not pk: return err('PK not found', 404)
    if g.user.id not in (pk.host1_id, pk.host2_id) and g.user.tg_id not in ADMIN_IDS: return err('Not a participant', 403)
    finalize_pk(pk); return jsonify(ok=1, status=pk.status, winner=pk.winner_id)
@app.get('/api/pk/active')
@auth
def pk_active():
    pks = PKBattle.query.filter_by(status='active').order_by(PKBattle.id.desc()).limit(20).all(); out = []
    for pk in pks:
        if now() > pk.started_at + pk.duration: finalize_pk(pk); continue
        h1 = db.session.get(User, pk.host1_id); h2 = db.session.get(User, pk.host2_id) if pk.host2_id else None
        out.append(dict(id=pk.id, host1=pub(h1) if h1 else None, host2=pub(h2) if h2 else None, host1_gifts=pk.host1_gifts or 0, host2_gifts=pk.host2_gifts or 0, time_left=max(0, pk.started_at + pk.duration - now())))
    return jsonify(pks=out)
@app.get('/api/pk/history')
@auth
def pk_history():
    limit = max(1, min(50, num(request.args.get('limit'), 20)))
    before = num(request.args.get('before'))
    q = PKBattle.query.filter(
        PKBattle.status == 'ended',
        or_(PKBattle.host1_id == g.user.id, PKBattle.host2_id == g.user.id)
    )
    if before > 0: q = q.filter(PKBattle.id < before)
    rows = q.order_by(PKBattle.ended_at.desc(), PKBattle.id.desc()).limit(limit).all()
    ids = {x.host1_id for x in rows} | {x.host2_id for x in rows if x.host2_id}
    users = {x.id: x for x in User.query.filter(User.id.in_(ids or {0})).all()}
    items = []
    for pk in rows:
        mine_first = pk.host1_id == g.user.id
        mine = pk.host1_gifts or 0 if mine_first else pk.host2_gifts or 0
        other = pk.host2_gifts or 0 if mine_first else pk.host1_gifts or 0
        opponent_id = pk.host2_id if mine_first else pk.host1_id
        outcome = 'win' if pk.winner_id == g.user.id else 'draw' if pk.winner_id is None and pk.host2_id and mine == other else 'no_winner' if pk.winner_id is None else 'loss'
        items.append(dict(
            id=pk.id, opponent=pub(users[opponent_id]) if opponent_id in users else None,
            my_gifts=mine, opponent_gifts=other, outcome=outcome,
            winner_id=pk.winner_id, started_at=pk.started_at, ended_at=pk.ended_at,
            duration=pk.duration
        ))
    return jsonify(items=items, next_before=items[-1]['id'] if len(items) == limit else None)
_pk_cache = {}


def pk_feed(pk):
    """Recent gifts (for animations) and the top 3 supporters of each side."""
    c = _pk_cache.get(pk.id)
    if c and time.time() - c[0] < 1.5: return c[1], c[2]
    rows = Tx.query.filter(Tx.kind == 'pk_gift', Tx.ts >= pk.started_at - pk.duration - 120,
                           Tx.note.like(f'PK#{pk.id} side%')).order_by(Tx.id).all()
    parsed = []
    for r in rows:
        w = r.note.split()
        side = 2 if len(w) > 1 and w[1] == 'side2' else 1
        parsed.append((r.id, side, r.user_id, w[2] if len(w) > 3 else '', num(w[3], 1) if len(w) > 3 else 1, -r.amount))
    totals = {1: {}, 2: {}}
    for _, side, uid, _, _, amount in parsed: totals[side][uid] = totals[side].get(uid, 0) + amount
    best = {side: sorted(t.items(), key=lambda x: -x[1])[:3] for side, t in totals.items()}
    last = parsed[-15:]
    ids = {x[2] for x in last} | {uid for l in best.values() for uid, _ in l}
    users = {x.id: x for x in User.query.filter(User.id.in_(list(ids) or [0])).all()}
    events = []
    for eid, side, uid, key, qty, amount in last:
        gf = GIFT_MAP.get(key) or {}
        events.append(dict(id=eid, side=side, name=users[uid].username if uid in users else '?', emoji=gf.get('emoji', '🎁'),
                           gift=key, tier=gf.get('tier', 'common'), qty=qty, total=amount))
    top = {str(side): [dict(pub(users[uid]), v=v) for uid, v in l if uid in users] for side, l in best.items()}
    if len(_pk_cache) > 100: _pk_cache.clear()
    _pk_cache[pk.id] = (time.time(), events, top)
    return events, top


@app.get('/api/pk/<int:pid>/state')
@auth
def pk_state(pid):
    pk = db.session.get(PKBattle, pid)
    if not pk: return err('PK not found', 404)
    if pk.status == 'active' and now() > pk.started_at + pk.duration: finalize_pk(pk)
    h1 = db.session.get(User, pk.host1_id); h2 = db.session.get(User, pk.host2_id) if pk.host2_id else None
    events, top = pk_feed(pk)
    return jsonify(id=pk.id, status=pk.status, events=events, top=top, duration=pk.duration, host1=pub(h1) if h1 else None, host2=pub(h2) if h2 else None, host1_gifts=pk.host1_gifts or 0, host2_gifts=pk.host2_gifts or 0,
                   time_left=max(0, pk.started_at + pk.duration - now()), winner=pk.winner_id)
@app.post('/api/pk/token')
@auth
def pk_token():
    pk = db.session.get(PKBattle, num(body().get('pk_id')))
    if not pk: return err('PK not found', 404)
    is_host = g.user.id in (pk.host1_id, pk.host2_id); channel = f'pk_{pk.id}'; token = None
    if AGORA_APP_CERT: token = RtcTokenBuilder.buildTokenWithUid(AGORA_APP_ID, AGORA_APP_CERT, channel, g.user.id, 1 if is_host else 2, now() + 3600)
    return jsonify(app_id=AGORA_APP_ID, channel=channel, token=token, uid=g.user.id, publisher=is_host, host1=pk.host1_id, host2=pk.host2_id)


@app.get('/api/lucky/info')
@auth
def lucky_info():
    st = db.session.get(LuckyStats, today())
    return jsonify(volume=st.volume if st else 0, returned=st.returned if st else 0, house=st.house if st else 0,
                   table=[[m, p] for m, p in LUCKY_TABLE], rtp=0.70, house_edge=0.30, gifts=[x for x in GIFTS_PUB if x['tier'] == 'lucky'])


POLICY = {
    'terms': "TERMS OF SERVICE\n1. Twenty One Party is a voice/live entertainment platform. Users must be 18+.\n2. One account per person.\n3. Coins are virtual currency; only earned income/commission is withdrawable.\n4. Purchases are final except where required by law.\n5. Violations lead to suspension or ban.",
    'commission': "HOST EARNINGS & 7-GENERATION COMMISSION\n1. Hosts receive 50% of the purchased-coin value of gifts. Free, bonus and reward coins pay no earnings or commission.\n2. When someone you invited receives a gift, you can earn: G1 5%, G2 2.5%, G3 1.5%, G4 1%, G5 0.75%, G6 0.75%, G7 0.5% (12% in total), from the platform's side. It does not reduce the receiver's earnings.\n3. Fractions of a coin are saved and paid when they add up to 1 coin.\n4. No payment for registration alone. No commission on your own gift, to banned accounts, on Lucky gifts or on PK gifts.",
    'seller': "COIN SELLER (RESELLER) POLICY\n1. Approved sellers buy coins at wholesale: Starter 0.42, Silver 0.41, Gold 0.40 Birr/coin.\n2. Minimum order: Starter 5,000 / Silver 20,000 / Gold 100,000 Birr.\n3. Bonus coins are PROMO: usable for own gifting, NOT transferable, NOT withdrawable.\n4. Sellers transfer trade coins to buyers; a 2% network fee applies.\n5. Recommended retail price 0.50 Birr/coin. Sellers keep the difference.\n6. Daily transfer limit 200,000 coins. Self-transfer is forbidden.\n7. Fraud, fake payment proofs or chargebacks lead to permanent ban and forfeiture.",
    'lucky': "LUCKY GIFT POLICY\n1. Lucky gifts are chance games: sending one may return a multiplier to the sender.\n2. 70% of lucky volume returns to players; 30% retained by platform.\n3. Lucky gifts add hype/stage score but do not pay host earnings.\n4. 18+ only. Play responsibly.",
    'pk': "PK BATTLE POLICY\n1. Two hosts battle 5 minutes; viewers gift each side.\n2. Winner 70%, loser 20%, platform 10%. Tie 45/45/10.",
    'stage': "LIVE GUESTS\n1. A LIVE host may invite up to 3 guests on stage.\n2. The host can mute or remove a guest; a guest can leave anytime.\n3. No nudity, harassment or explicit content — instant removal and ban.",
    'withdraw': f"WITHDRAWAL & PAYMENTS\n1. Telebirr: 1 coin = {COIN_BIRR} ETB, min {MIN_WITHDRAW} coins.\n2. USDT (TRC20): 1 coin = {COIN_USDT} USDT. Provide a valid T… address (34 chars).\n3. Rate: 1 USDT = {USDT_BIRR} ETB.\n4. TX id/hash + screenshot required for purchases.\n5. 24–72h processing after approval.",
    'conduct': "COMMUNITY RULES\n1. No hate, harassment, threats.\n2. No minor sexual content — permanent ban + reporting.\n3. No nudity/explicit live content.\n4. No scams, bots, cheating.\n5. Report to @TwentyOneOwner.",
    'privacy': "PRIVACY\n1. We collect Telegram ID, name, optional phone/wallet address, usage data.\n2. Photos stored only for display.\n3. We never sell data.\n4. Account deletion via support."
}
@app.get('/api/policy')
def policy_get(): return jsonify(sections=POLICY)


@app.get('/api/admin/overview')
@admin_only
def adm_overview():
    t = now(); st = db.session.get(LuckyStats, today())
    return jsonify(users=User.query.count(), banned=User.query.filter_by(banned=True).count(), rooms=Room.query.count(),
                   online=db.session.query(func.count(func.distinct(Presence.user_id))).filter(Presence.seen > t - 20).scalar() or 0,
                   on_stage=LiveGuest.query.filter_by(status='live').count(),
                   vip=User.query.filter(User.vip_until > t).count(), pending_buy=Purchase.query.filter_by(status='pending').count(), pending_wd=Withdraw.query.filter_by(status='pending').count(),
                   coins_total=db.session.query(func.coalesce(func.sum(User.coins), 0)).scalar(), earn_total=db.session.query(func.coalesce(func.sum(User.earnings), 0)).scalar(),
                   revenue=db.session.query(func.coalesce(func.sum(Purchase.birr), 0)).filter(Purchase.status == 'approved').scalar(),
                   paid=db.session.query(func.coalesce(func.sum(Withdraw.birr), 0)).filter(Withdraw.status == 'paid').scalar(),
                   agents=User.query.filter_by(is_agent=True).count(), pending_host=User.query.filter_by(host_status='pending').count(),
                   commission_total=db.session.query(func.coalesce(func.sum(User.commission), 0)).scalar(),
                   lucky_volume=st.volume if st else 0, lucky_house=st.house if st else 0, usdt_rate=USDT_BIRR)
@app.get('/api/admin/purchases')
@admin_only
def adm_purchases():
    ps = Purchase.query.filter_by(status=request.args.get('status', 'pending')).order_by(Purchase.id.desc()).limit(50).all()
    return jsonify(items=[dict(id=p.id, user_id=p.user_id, name=p.username, coins=p.coins, birr=p.birr, ref=(p.txhash or p.ref), ts=p.ts, method=p.method, usdt=round(p.birr/USDT_BIRR, 2)) for p in ps])
@app.get('/api/admin/withdraws')
@admin_only
def adm_withdraws():
    ws = Withdraw.query.filter_by(status=request.args.get('status', 'pending')).order_by(Withdraw.id.desc()).limit(50).all()
    return jsonify(items=[dict(id=w.id, user_id=w.user_id, name=w.username, coins=w.coins, birr=w.birr, phone=w.phone, ts=w.ts, method=w.method, address=(w.usdt_address or w.phone), usdt=round(w.coins*COIN_USDT, 4)) for w in ws])
@app.get('/api/admin/users')
@admin_only
def adm_users():
    q = (request.args.get('q') or '').strip(); qs = User.query
    if q: qs = qs.filter(or_(User.username.ilike(f'%{q}%'), User.tg_id == q, User.id.in_(AUTH['ids_by_phone'](q) or [0])))
    us = qs.order_by(User.id.desc()).limit(40).all()
    phones = AUTH['phones_for']([u.id for u in us])
    return jsonify(items=[dict(id=u.id, login_phone=phones.get(u.id, ''), tg=u.tg_id, name=u.username, coins=u.coins, earn=u.earnings, banned=u.banned, vip=vip_active(u), host=u.host_status, ag=u.is_agent, comm=u.commission or 0) for u in us])
@app.get('/api/admin/shot/<int:pid>')
@admin_only
def adm_shot(pid):
    s = db.session.get(Shot, pid)
    if not s: return Response(status=404)
    return Response(s.data, mimetype=s.mime)
@app.post('/api/admin/purchase')
@admin_only
def adm_purchase():
    b = body(); pid, act = num(b.get('id')), b.get('action'); p = db.session.get(Purchase, pid)
    if not p: return err('not found', 404)
    new = 'approved' if act == 'approve' else 'rejected'
    r = db.session.execute(update(Purchase).where(Purchase.id == pid, Purchase.status == 'pending').values(status=new), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); return err('Already processed')
    u = db.session.get(User, p.user_id)
    if act == 'approve':
        add_coins(p.user_id, p.coins)
        db.session.execute(update(User).where(User.id == p.user_id).values(real_coins=User.real_coins + p.coins), execution_options={'synchronize_session': False})
        tx(p.user_id, 'buy', p.coins, f'purchase #{p.id}')
        FIN['ledger'](p.user_id, 'purchase', p.coins, 'birr', p.id, f'{p.birr} ETB')
        if u and u.referrer_id:
            bonus = p.coins // 10; add_coins(u.referrer_id, bonus); tx(u.referrer_id, 'referral', bonus, f'from {u.id}')
            ru = db.session.get(User, u.referrer_id); notify(ru.tg_id if ru else None, f'🎁 Referral +{bonus} coins')
        if u and u.agent_id and not Tx.query.filter_by(user_id=p.user_id, kind='invite_reward').count():
            add_coins(u.agent_id, INVITE_REWARD); tx(u.agent_id, 'invite', INVITE_REWARD, f'invite {u.id}'); tx(p.user_id, 'invite_reward', 0, f'agent {u.agent_id}')
            FIN['referral_qualify'](p.user_id, 'first_purchase', INVITE_REWARD)
    db.session.commit()
    if act == 'approve':
        FIN['wallet_sync'](p.user_id)
        if u and u.agent_id: FIN['wallet_sync'](u.agent_id)
    if u: notify(u.tg_id, f'✅ {p.coins} coins added!' if act == 'approve' else '❌ Purchase rejected.')
    return jsonify(ok=1)
@app.post('/api/admin/withdraw')
@admin_only
def adm_withdraw():
    b = body(); wid, act = num(b.get('id')), b.get('action'); w = db.session.get(Withdraw, wid)
    if not w: return err('not found', 404)
    new = 'paid' if act == 'paid' else 'rejected'
    r = db.session.execute(update(Withdraw).where(Withdraw.id == wid, Withdraw.status == 'pending').values(status=new), execution_options={'synchronize_session': False})
    if r.rowcount != 1:
        db.session.rollback(); return err('Already processed')
    u = db.session.get(User, w.user_id)
    if act != 'paid':
        # Legacy requests have zero source fields; refund those to earnings as before.
        refund_earnings = int(w.earnings_coins or 0)
        refund_commission = int(w.commission_coins or 0)
        if refund_earnings + refund_commission == 0:
            refund_earnings = int(w.coins or 0)
        db.session.execute(update(User).where(User.id == w.user_id).values(
            earnings=User.earnings + refund_earnings, commission=User.commission + refund_commission), execution_options={'synchronize_session': False})
        tx(w.user_id, 'refund', w.coins, f'withdraw #{w.id} rejected (earnings {refund_earnings}, commission {refund_commission})')
    db.session.commit()
    if u: notify(u.tg_id, '💰 Payment sent!' if act == 'paid' else f'❌ Rejected; {w.coins} refunded.')
    return jsonify(ok=1)
@app.post('/api/admin/user')
@admin_only
def adm_user():
    b = body(); uid, act = num(b.get('id')), b.get('action'); u = db.session.get(User, uid)
    if not u: return err('not found', 404)
    if u.tg_id in ADMIN_IDS and act in ('ban', 'delete'): return err('Cannot act on admin')
    if act == 'ban': u.banned = True
    elif act == 'unban': u.banned = False
    elif act == 'grant':
        n = num(b.get('amount'))
        if not -1000000 <= n <= 1000000 or n == 0: return err('bad amount')
        add_coins(uid, n); tx(uid, 'grant', n, 'admin')
    elif act == 'vip':
        d = num(b.get('amount'))
        if not 1 <= d <= 365: return err('bad days')
        u.vip_until = max(u.vip_until or 0, now()) + d * 86400; tx(uid, 'vip', 0, f'admin {d}d')
    elif act == 'host': u.host_status = 'host' if num(b.get('amount'), 1) else 'none'
    elif act == 'agent':
        u.is_agent = not u.is_agent
        if u.is_agent and not u.parent_agent_id:
            r = db.session.get(User, u.agent_id) if u.agent_id else None; u.parent_agent_id = r.id if r and r.is_agent else None
    elif act == 'delete':
        for M in (Seat, Presence, Post, Job, Photo, UserFrame, LiveGuest): M.query.filter(M.user_id == uid).delete(synchronize_session=False)
        AUTH['delete_login'](uid)
        FEED['delete_follows'](uid)
        db.session.delete(u)
    else: return err('bad action')
    db.session.commit(); return jsonify(ok=1)
@app.post('/api/admin/room')
@admin_only
def adm_room():
    delete_room(str(body().get('id'))); return jsonify(ok=1)


try:
    from .feed import init_feed
except ImportError:
    from feed import init_feed

FEED = init_feed(app, db, dict(
    User=User, Room=Room, Seat=Seat, Presence=Presence, Tx=Tx, now=now, err=err, body=body, num=num,
    auth=auth, pub=pub, limited=limited, gc_rooms=gc_rooms, admin_only=admin_only))


try:
    from .host_rewards import init_host_rewards
except ImportError:
    from host_rewards import init_host_rewards

HOST_REWARDS = init_host_rewards(app, db, dict(
    User=User, Room=Room, Presence=Presence, now=now, tx=tx, auth=auth
))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)), debug=False, threaded=True)
