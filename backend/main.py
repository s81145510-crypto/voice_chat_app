# -*- coding: utf-8 -*-
"""Twenty One Party - PRO backend + PWA Support + Guest Login."""
import os, json, hmac, hashlib, time, random, secrets, threading, urllib.request, urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, Response, g, send_from_directory
from flask_sqlalchemy import SQLAlchemy
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
AGENT_RATES = (0.30, 0.15, 0.05)
MAX_AGENT_DEPTH = 3
INVITE_REWARD = 50
LUCKY_TABLE = [(0, .74), (1, .12), (2, .08), (5, .04), (10, .015), (14, .005)]
PK_WINNER_SHARE, PK_LOSER_SHARE, PK_PLATFORM_SHARE = 0.70, 0.20, 0.10
PK_DURATION = 300
SHARE = {'luxury': 0.70, 'legendary': 0.04, 'mythic': 0.04}
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
    parent_agent_id = db.Column(db.Integer, nullable=True); commission = db.Column(db.Integer, default=0, nullable=False)


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
    ref = db.Column(db.String(40), unique=True); status = db.Column(db.String(10), default='pending', index=True); ts = db.Column(db.Integer, default=now)
    method = db.Column(db.String(10), default='telebirr'); txhash = db.Column(db.String(80), default='')


class Shot(db.Model):
    purchase_id = db.Column(db.Integer, primary_key=True); data = db.Column(db.LargeBinary); mime = db.Column(db.String(30))


class Withdraw(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, index=True); username = db.Column(db.String(60)); coins = db.Column(db.Integer); birr = db.Column(db.Integer)
    phone = db.Column(db.String(20)); status = db.Column(db.String(10), default='pending', index=True); ts = db.Column(db.Integer, default=now)
    method = db.Column(db.String(10), default='telebirr'); usdt_address = db.Column(db.String(64), default='')


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
    started_at = db.Column(db.Integer, default=now); ended_at = db.Column(db.Integer, nullable=True); duration = db.Column(db.Integer, default=PK_DURATION); winner_id = db.Column(db.Integer, nullable=True)


class LuckyStats(db.Model):
    day = db.Column(db.String(10), primary_key=True); volume = db.Column(db.Integer, default=0); returned = db.Column(db.Integer, default=0); house = db.Column(db.Integer, default=0)


def ensure_columns():
    from sqlalchemy import inspect
    want = {User: [('vip_until', 'INTEGER DEFAULT 0'), ('frame', "VARCHAR(20) DEFAULT ''"), ('real_coins', 'INTEGER DEFAULT 0 NOT NULL'),
                   ('host_status', "VARCHAR(8) DEFAULT 'none'"), ('is_agent', 'BOOLEAN DEFAULT FALSE'), ('agent_id', 'INTEGER'), ('parent_agent_id', 'INTEGER'), ('commission', 'INTEGER DEFAULT 0 NOT NULL')],
            Room: [('kind', "VARCHAR(8) DEFAULT 'voice'")], Post: [('has_image', 'BOOLEAN DEFAULT FALSE')], Job: [('has_image', 'BOOLEAN DEFAULT FALSE')],
            PKBattle: [('winner_id', 'INTEGER')],
            Purchase: [('method', "VARCHAR(10) DEFAULT 'telebirr'"), ('txhash', "VARCHAR(80) DEFAULT ''")],
            Withdraw: [('method', "VARCHAR(10) DEFAULT 'telebirr'"), ('usdt_address', "VARCHAR(64) DEFAULT ''")]}
    insp = inspect(db.engine)
    for M, cols in want.items():
        tbl = M.__tablename__; have = {c['name'] for c in insp.get_columns(tbl)}
        for name, ddl in cols:
            if name in have: continue
            try:
                db.session.execute(sa_text(f'ALTER TABLE "{tbl}" ADD COLUMN {name} {ddl}')); db.session.commit()
            except Exception as e:
                db.session.rollback(); print('ensure_columns:', tbl, name, e)


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
    p = f'{uid}.{now() + days * 86400}'; return p + '.' + hmac.new(SECRET, p.encode(), hashlib.sha256).hexdigest()[:32]
def parse_token(t):
    try:
        uid, exp, sig = t.split('.')
        ok = hmac.compare_digest(sig, hmac.new(SECRET, f'{uid}.{exp}'.encode(), hashlib.sha256).hexdigest()[:32])
        return int(uid) if ok and int(exp) > now() else None
    except Exception: return None
def verify_init(init):
    data = dict(parse_qsl(init, keep_blank_values=True)); h = data.pop('hash', None)
    if not h or not BOT_TOKEN: return None
    check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
    key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h): return None
    if now() - num(data.get('auth_date')) > 86400 * 3: return None
    try: return json.loads(data['user']), data
    except Exception: return None
def auth(f):
    @wraps(f)
    def w(*a, **k):
        h = request.headers.get('Authorization', ''); uid = parse_token(h[7:]) if h.startswith('Bearer ') else None
        u = db.session.get(User, uid) if uid else None
        if not u: return err('unauthorized', 401)
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


_TPL = next((p for p in (os.path.join(BASE, 'templates', 'index.html'), os.path.join(BASE, '..', 'templates', 'index.html')) if os.path.exists(p)), os.path.join(BASE, 'templates', 'index.html'))
_HTML = open(_TPL, encoding='utf-8').read()
@app.get('/')
def index(): return Response(_HTML, mimetype='text/html', headers={'Cache-Control': 'no-cache'})
@app.get('/healthz')
def healthz():
    db.session.execute(sa_text('select 1')); return 'ok'

# ================= PWA ROUTES =================
@app.get('/manifest.json')
def manifest():
    try:
        return Response(open(os.path.join(BASE, 'templates', 'manifest.json'), encoding='utf-8').read(), mimetype='application/manifest+json', headers={'Cache-Control': 'no-cache'})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.get('/sw.js')
def sw():
    try:
        return Response(open(os.path.join(BASE, 'templates', 'sw.js'), encoding='utf-8').read(), mimetype='application/javascript', headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.get('/static/<path:fn>')
def staticf(fn):
    return send_from_directory(os.path.join(BASE, 'static'), fn)
# ==============================================

try:
    from .finance import init_finance
except ImportError:
    from finance import init_finance

FIN = init_finance(app, db, dict(
    User=User, Tx=Tx, Purchase=Purchase, Withdraw=Withdraw, LuckyStats=LuckyStats, PKBattle=PKBattle,
    now=now, add_coins=add_coins, tx=tx, coin_birr=lambda: COIN_BIRR, usdt_birr=lambda: USDT_BIRR,
    is_admin=lambda u: u.tg_id in ADMIN_IDS, err=err, body=body, num=num, auth=auth, admin_only=admin_only,
    jsonify=jsonify, request=request))

GIFTS_PUB = [GIFT_MAP[k] for k, *_ in GIFTS]
FRAMES_PUB = list(FRAME_MAP.values())
def cfg():
    return dict(agora_app_id=AGORA_APP_ID, gifts=GIFTS_PUB, packages=[dict(coins=c, birr=b) for c, b in PACKAGES], levels=LEVELS, pay_number=PAY_NUMBER, bot=BOT_USERNAME,
                min_withdraw=MIN_WITHDRAW, rate=COIN_BIRR, seats=SEATS, max_guests=MAX_LIVE_GUESTS, min_bet=MIN_BET, max_bet=MAX_BET, p2p_mult=P2P_MULT, frames=FRAMES_PUB,
                channel=CHANNEL_URL, support=SUPPORT,
                host=dict(share=HOST_SHARE), agent=dict(rates=list(AGENT_RATES), depth=MAX_AGENT_DEPTH), invite=dict(reward=INVITE_REWARD),
                vip=dict(price=VIP_PRICE, days=VIP_DAYS, bonus=VIP_DAILY_BONUS, rooms=VIP_ROOMS),
                lucky=dict(table=[[m, p] for m, p in LUCKY_TABLE], rtp=0.70, house=0.30),
                pk=dict(winner_share=PK_WINNER_SHARE, loser_share=PK_LOSER_SHARE, platform_share=PK_PLATFORM_SHARE, duration=PK_DURATION),
                usdt=dict(address=USDT_ADDRESS, network='TRC20', birr_per_usdt=USDT_BIRR, coin_usdt=COIN_USDT))


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
    elif DEV:
        tgid = str(b.get('dev_id') or '1'); name = 'Dev ' + tgid; photo = ''
    else:
        return err('Open in Telegram or use Guest mode', 401)
    u = User.query.filter_by(tg_id=tgid).first()
    if not u:
        ref = None; ag = None
        if start.startswith('ref_'):
            r = User.query.filter_by(tg_id=start[4:]).first(); ref = r.id if r else None
        elif start.startswith('ag_'):
            a = User.query.filter_by(tg_id=start[3:]).first(); ag = a.id if a and a.is_agent else None
        u = User(tg_id=tgid, username=name[:40], coins=WELCOME, tg_photo=photo, referrer_id=ref, agent_id=ag)
        db.session.add(u)
        try:
            db.session.flush(); tx(u.id, 'welcome', WELCOME, 'Welcome bonus')
            if ref: FIN['referral_register'](ref, u.id)
            if ag: FIN['referral_register'](ag, u.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); u = User.query.filter_by(tg_id=tgid).first()
    elif photo and u.tg_photo != photo:
        u.tg_photo = photo; db.session.commit()
    if u.banned: return err('Your account is banned', 403)
    return jsonify(token=sign(u.id), me=me_dict(u), cfg=cfg())
