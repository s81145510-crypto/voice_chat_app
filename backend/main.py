from flask import Flask, render_template, request, jsonify, Response, g
import json, os, re, hmac, hashlib, time, uuid, base64, random
from functools import wraps
from urllib.parse import parse_qsl
from datetime import datetime
from livekit import api as lkapi

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'db.json')
SHOT_DIR = os.path.join(DATA_DIR, 'shots')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
ADMIN_IDS = {x.strip() for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()} | {'6746329545'}

TELEBIRR_NUMBER = '0970575151'
TELEBIRR_NAME = 'Firew'

MAX_SEATS = 21
STALE_SECONDS = 45
WELCOME_COINS = 100
RECEIVER_SHARE = 0.7
WITHDRAW_RATE_ETB = 0.4
MIN_WITHDRAW = 500
REFERRAL_BONUS_PERCENT = 10

ID_RE = re.compile(r'^[A-Za-z0-9_]{1,40}$')
REF_RE = re.compile(r'^[A-Za-z0-9]{8,20}$')

GIFTS = {
    'heart':    {'name': 'Heart',    'price': 5,     'icon': '❤️', 'rarity': 'common', 'luxury': False},
    'coffee':   {'name': 'Coffee',   'price': 10,    'icon': '☕', 'rarity': 'common', 'luxury': False},
    'candy':    {'name': 'Candy',    'price': 20,    'icon': '🍭', 'rarity': 'common', 'luxury': False},
    'rose':     {'name': 'Rose',     'price': 50,    'icon': '🌹', 'rarity': 'rare',   'luxury': False},
    'choco':    {'name': 'Chocolate','price': 80,    'icon': '🍫', 'rarity': 'rare',   'luxury': False},
    'bear':     {'name': 'Bear',     'price': 100,   'icon': '🧸', 'rarity': 'rare',   'luxury': False},
    'cake':     {'name': 'Cake',     'price': 200,   'icon': '🎂', 'rarity': 'epic',   'luxury': True},
    'champ':    {'name': 'Champagne','price': 300,   'icon': '🍾', 'rarity': 'epic',   'luxury': True},
    'diamond':  {'name': 'Diamond',  'price': 500,   'icon': '💎', 'rarity': 'epic',   'luxury': True},
    'crown':    {'name': 'Crown',    'price': 800,   'icon': '👑', 'rarity': 'legendary', 'luxury': True},
    'car':      {'name': 'Car',      'price': 1000,  'icon': '🚗', 'rarity': 'legendary', 'luxury': True},
    'ring':     {'name': 'Ring',     'price': 1500,  'icon': '💍', 'rarity': 'legendary', 'luxury': True},
    'watch':    {'name': 'Watch',    'price': 2000,  'icon': '⌚', 'rarity': 'legendary', 'luxury': True},
    'bike':     {'name': 'Motorbike','price': 3000,  'icon': '🏍️', 'rarity': 'mythic', 'luxury': True},
    'boat':     {'name': 'Boat',     'price': 4000,  'icon': '🛥️', 'rarity': 'mythic', 'luxury': True},
    'castle':   {'name': 'Castle',   'price': 5000,  'icon': '🏰', 'rarity': 'mythic', 'luxury': True},
    'plane':    {'name': 'Plane',    'price': 7500,  'icon': '✈️', 'rarity': 'mythic', 'luxury': True},
    'rocket':   {'name': 'Rocket',   'price': 10000, 'icon': '🚀', 'rarity': 'mythic', 'luxury': True},
    'island':   {'name': 'Island',   'price': 20000, 'icon': '🏝️', 'rarity': 'mythic', 'luxury': True},
    'universe': {'name': 'Universe', 'price': 50000, 'icon': '🌌', 'rarity': 'mythic', 'luxury': True},
}

COIN_PACKAGES = [
    {'id': 'p1', 'coins': 100,   'price': 50},
    {'id': 'p2', 'coins': 500,   'price': 240},
    {'id': 'p3', 'coins': 1000,  'price': 470},
    {'id': 'p4', 'coins': 2500,  'price': 1150},
    {'id': 'p5', 'coins': 5000,  'price': 2250},
    {'id': 'p6', 'coins': 10000, 'price': 4400},
    {'id': 'p7', 'coins': 50000, 'price': 21000},
]

AGENCY_LEVELS = {
    'D':  {'min': 0,              'rate': 0.08, 'name': 'D', 'color': '#7bed9f'},
    'C':  {'min': 20_000_000,     'rate': 0.12, 'name': 'C', 'color': '#1e90ff'},
    'B':  {'min': 100_000_000,    'rate': 0.16, 'name': 'B', 'color': '#a55eea'},
    'A':  {'min': 500_000_000,    'rate': 0.18, 'name': 'A', 'color': '#ffd700'},
    'S':  {'min': 1_500_000_000,  'rate': 0.20, 'name': 'S', 'color': '#ff4757'},
    'S+': {'min': 3_000_000_000,  'rate': 0.21, 'name': 'S+', 'color': '#ff6348'},
}

GAMES = {
    'dice': {'name': 'Dice Roll', 'icon': '🎲', 'min_bet': 10, 'max_bet': 10000, 'multiplier': 4.2},
    'flip': {'name': 'Coin Flip', 'icon': '🪙', 'min_bet': 10, 'max_bet': 50000, 'multiplier': 1.4},
    'wheel': {'name': 'Lucky Wheel', 'icon': '🎡', 'min_bet': 10, 'max_bet': 100000,
              'segments': [
                  {'mult': 0,   'weight': 42, 'label': '0x',  'color': '#ff4757'},
                  {'mult': 0.5, 'weight': 25, 'label': '0.5x','color': '#ffa502'},
                  {'mult': 1,   'weight': 17, 'label': '1x',  'color': '#2ed573'},
                  {'mult': 2,   'weight': 10, 'label': '2x',  'color': '#1e90ff'},
                  {'mult': 3,   'weight': 4,  'label': '3x',  'color': '#a55eea'},
                  {'mult': 5,   'weight': 2,  'label': '5x',  'color': '#ffd700'},
              ]},
    'race': {'name': 'Horse Race', 'icon': '🏇', 'min_bet': 10, 'max_bet': 50000, 'multiplier': 4.5},
    'box': {'name': 'Mystery Box', 'icon': '📦', 'min_bet': 50, 'max_bet': 100000,
            'boxes': [
                {'mult': 0,   'weight': 50, 'label': 'Empty',  'icon': '💨'},
                {'mult': 0.5, 'weight': 25, 'label': 'Small',  'icon': '🎁'},
                {'mult': 2,   'weight': 15, 'label': 'Medium', 'icon': '🎊'},
                {'mult': 5,   'weight': 7,  'label': 'Large',  'icon': '💎'},
                {'mult': 10,  'weight': 3,  'label': 'Jackpot','icon': '👑'},
            ]},
    'p2p': {'name': 'P2P Battle', 'icon': '⚔️', 'min_bet': 100, 'max_bet': 1000000, 'multiplier': 1.9},
}

# ============ HELPERS ============
def verify_init_data(init_data):
    try:
        if not BOT_TOKEN:
            return None
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop('hash', None)
        if not received:
            return None
        check = '\n'.join(f'{k}={v}' for k, v in sorted(pairs.items()))
        secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get('auth_date', 0)) > 86400:
            return None
        return json.loads(pairs['user'])
    except Exception:
        return None

def is_admin(uid):
    return bool(uid) and uid.replace('tg_', '') in ADMIN_IDS

def load_data():
    data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
        except Exception:
            data = {}
    for k, v in (('users', {}), ('rooms', {}), ('transactions', []), ('purchases', []),
                 ('withdrawals', []), ('posts', []), ('jobs', []), ('games', []),
                 ('p2p_games', []), ('agency_commissions', []),
                 ('meta', {'fee_coins': 0, 'gift_count': 0, 'game_profit': 0, 'game_total_bet': 0})):
        data.setdefault(k, v)
    data['gifts'] = GIFTS
    return data

def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, DATA_FILE)

def current_user_id():
    tg = verify_init_data(request.headers.get('X-Telegram-Init-Data', ''))
    if not tg:
        return None, None
    uid = f"tg_{tg['id']}"
    if not is_admin(uid):
        u = load_data()['users'].get(uid)
        if u and u.get('banned'):
            g.banned = True
            return None, None
    return uid, tg

def unauthorized():
    if getattr(g, 'banned', False):
        return jsonify({'success': False, 'error': 'banned', 'banned': True}), 403
    return jsonify({'success': False, 'error': 'Unauthorized'}), 401

def forbidden():
    return jsonify({'success': False, 'error': 'Forbidden'}), 403

def err(msg):
    return jsonify({'success': False, 'error': msg})

def admin_only(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        uid, _ = current_user_id()
        if not uid:
            return unauthorized()
        if not is_admin(uid):
            return forbidden()
        return fn(uid, *a, **k)
    return wrapper

def display_name(tg):
    return (tg.get('first_name') or 'User')[:30]

def photo_of(tg):
    url = tg.get('photo_url') or ''
    return url if url.startswith('https://') else ''

def now_iso():
    return datetime.now().isoformat(timespec='seconds')

def norm_phone(s):
    s = re.sub(r'[\s-]', '', str(s or ''))
    if s.startswith('+251'):
        s = '0' + s[4:]
    elif s.startswith('251') and len(s) == 12:
        s = '0' + s[3:]
    return s if re.fullmatch(r'0[79]\d{8}', s) else None

def save_shot(pid, data_url):
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.+)$', str(data_url or ''), re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    if len(raw) < 500 or len(raw) > 2_500_000:
        return None
    if raw[:2] == b'\xff\xd8':
        ext = 'jpg'
    elif raw[:4] == b'\x89PNG':
        ext = 'png'
    elif raw[:4] == b'RIFF':
        ext = 'webp'
    else:
        return None
    os.makedirs(SHOT_DIR, exist_ok=True)
    name = pid + '.' + ext
    with open(os.path.join(SHOT_DIR, name), 'wb') as f:
        f.write(raw)
    return name

def add_message(room, name, text, sys=False, gift=None):
    room['msg_seq'] = room.get('msg_seq', 0) + 1
    msg = {'id': room['msg_seq'], 'name': name, 'text': text, 'sys': sys}
    if gift:
        msg['gift'] = gift
    room.setdefault('messages', []).append(msg)
    room['messages'] = room['messages'][-100:]

def cleanup_room(room):
    now = time.time()
    members = room.setdefault('members', {})
    room.setdefault('seats', {})
    for uid in [u for u, m in members.items() if now - m['seen'] > STALE_SECONDS]:
        members.pop(uid)
    for k in [k for k, s in room['seats'].items() if s['uid'] not in members]:
        room['seats'].pop(k)
    if members:
        room['active'] = now

def remove_user_seats(room, uid):
    for k in [k for k, s in room['seats'].items() if s['uid'] == uid]:
        room['seats'].pop(k)

def purge_user_from_rooms(data, uid):
    for rid in [r for r, room in data['rooms'].items() if room.get('creator') == uid]:
        data['rooms'].pop(rid)
    for room in data['rooms'].values():
        room.setdefault('seats', {})
        room.setdefault('members', {})
        remove_user_seats(room, uid)
        room['members'].pop(uid, None)

def get_user_level(coins):
    if coins >= 100000: return {'name': 'King', 'icon': '👑', 'color': '#ffd700'}
    if coins >= 50000:  return {'name': 'Diamond', 'icon': '💎', 'color': '#b9f2ff'}
    if coins >= 10000:  return {'name': 'Gold', 'icon': '🥇', 'color': '#ffd700'}
    if coins >= 5000:   return {'name': 'Silver', 'icon': '🥈', 'color': '#c0c0c0'}
    if coins >= 1000:   return {'name': 'Bronze', 'icon': '🥉', 'color': '#cd7f32'}
    return {'name': 'Newbie', 'icon': '🌱', 'color': '#7bed9f'}

def get_agency_level(total_income):
    if total_income >= 3_000_000_000:
        return 'S+'
    for lvl in ['S','A','B','C','D']:
        if total_income >= AGENCY_LEVELS[lvl]['min']:
            return lvl
    return 'D'

def calc_agency_commission(data, uid):
    u = data['users'].get(uid)
    if not u:
        return {'level': 'D', 'rate': 0.08, 'salary': 0, 'total_income': 0, 'subs': []}
    direct_subs = [sub_uid for sub_uid, sub in data['users'].items() if sub.get('referrer') == uid]
    def get_sub_income(sub_uid, depth=0, visited=None):
        if visited is None:
            visited = set()
        if sub_uid in visited or depth > 100:
            return 0
        visited.add(sub_uid)
        sub = data['users'].get(sub_uid)
        if not sub:
            return 0
        sub_purchases = [p for p in data['purchases'] if p['uid'] == sub_uid and p['status'] == 'approved']
        sub_income = sum(p['coins'] for p in sub_purchases)
        sub_subs = [ss for ss, s in data['users'].items() if s.get('referrer') == sub_uid]
        for ss in sub_subs:
            sub_income += get_sub_income(ss, depth+1, visited)
        return sub_income
    total_income = 0
    sub_details = []
    for sub_uid in direct_subs:
        sub_income = get_sub_income(sub_uid)
        total_income += sub_income
        sub_level = get_agency_level(sub_income)
        sub_details.append({'uid': sub_uid, 'username': data['users'][sub_uid].get('username', ''),
                           'income': sub_income, 'level': sub_level})
    my_level = get_agency_level(total_income)
    my_rate = AGENCY_LEVELS[my_level]['rate']
    salary = 0
    for sub in sub_details:
        sub_rate = AGENCY_LEVELS[sub['level']]['rate']
        diff = my_rate - sub_rate
        if diff > 0:
            salary += int(diff * sub['income'])
    return {'level': my_level, 'rate': my_rate, 'salary': salary, 'total_income': total_income, 'subs': sub_details[:20]}

def user_view(u, data=None):
    level = get_user_level(u.get('coins', 0))
    agency_info = {'level': 'D', 'rate': 0.08, 'salary': 0, 'total_income': 0}
    if data:
        agency_info = calc_agency_commission(data, u.get('uid', ''))
    seller_level = None
    if data:
        user_purchases = [p for p in data.get('purchases', []) if p['uid'] == u.get('uid', '') and p['status'] == 'approved']
        total_usd = sum(p['price'] for p in user_purchases) / 210
        if total_usd >= 1000: seller_level = 'senior'
        elif total_usd >= 200: seller_level = 'standard'
        elif total_usd >= 100: seller_level = 'beginner'
    return {
        'username': u.get('username', ''), 'coins': u.get('coins', 0),
        'earnings': u.get('earnings', 0), 'bio': u.get('bio', ''),
        'phone': u.get('phone', ''), 'joined': u.get('joined', ''),
        'photo': u.get('photo', ''), 'level': level,
        'referrer': u.get('referrer', ''), 'referral_bonus': u.get('referral_bonus', 0),
        'referral_count': u.get('referral_count', 0),
        'total_gifts_sent': u.get('total_gifts_sent', 0),
        'total_gifts_received': u.get('total_gifts_received', 0),
        'gift_level': get_user_level(u.get('total_gifts_received', 0)),
        'agency': agency_info,
        'coin_seller': seller_level,
        'coin_seller_badge': {'beginner': '🥉 Beginner Seller', 'standard': '🥈 Standard Seller', 'senior': '🥇 Senior Seller'}.get(seller_level, ''),
    }

# ============ ROUTES ============
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    referrer = str(body.get('referrer', '')).strip()
    data = load_data()
    changed = False
    if uid not in data['users']:
        data['users'][uid] = {
            'username': display_name(tg), 'coins': WELCOME_COINS,
            'earnings': 0, 'joined': now_iso(), 'bio': '', 'phone': '',
            'referrer': referrer if referrer and referrer != uid else '',
            'referral_bonus': 0, 'referral_count': 0,
            'total_gifts_sent': 0, 'total_gifts_received': 0,
        }
        changed = True
    u = data['users'][uid]
    ph = photo_of(tg)
    if u.get('photo', '') != ph:
        u['photo'] = ph
        changed = True
    if changed:
        save_data(data)
    gifts = [dict(id=k, **v) for k, v in GIFTS.items()]
    games_cfg = {k: {kk: vv for kk, vv in v.items() if kk not in ('segments', 'boxes')} for k, v in GAMES.items()}
    agency_info = calc_agency_commission(data, uid)
    return jsonify({
        'success': True, 'user_id': uid, 'user': user_view(u, data), 'admin': is_admin(uid),
        'config': {
            'gifts': gifts, 'packages': COIN_PACKAGES,
            'telebirr': {'number': TELEBIRR_NUMBER, 'name': TELEBIRR_NAME},
            'rate': WITHDRAW_RATE_ETB, 'min_withdraw': MIN_WITHDRAW,
            'fee_percent': int(round((1 - RECEIVER_SHARE) * 100)),
            'seats': MAX_SEATS, 'referral_bonus': REFERRAL_BONUS_PERCENT,
            'games': games_cfg,
            'agency_levels': AGENCY_LEVELS,
            'app_name': 'Twenty One Party',
        }
    })

@app.route('/api/profile', methods=['POST'])
def profile():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('User not found')
    name = str(body.get('name', '')).strip()[:30]
    if name:
        u['username'] = name
    u['bio'] = str(body.get('bio', '')).strip()[:150]
    phone = str(body.get('phone', '')).strip()
    if phone:
        p = norm_phone(phone)
        if not p:
            return err('Invalid Telebirr number (09xxxxxxxx)')
        u['phone'] = p
    else:
        u['phone'] = ''
    save_data(data)
    return jsonify({'success': True, 'user': user_view(u, data)})

@app.route('/api/profile/photo', methods=['POST'])
def profile_photo():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    photo_data = body.get('photo')
    if not photo_data:
        return err('No photo')
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.+)$', str(photo_data), re.S)
    if not m:
        return err('Invalid photo')
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return err('Photo decode failed')
    if len(raw) < 500 or len(raw) > 2_500_000:
        return err('Photo too large')
    if raw[:2] == b'\xff\xd8':
        ext = 'jpg'
    elif raw[:4] == b'\x89PNG':
        ext = 'png'
    elif raw[:4] == b'RIFF':
        ext = 'webp'
    else:
        return err('Invalid photo type')
    os.makedirs(SHOT_DIR, exist_ok=True)
    filename = f'profile_{uid}.{ext}'
    with open(os.path.join(SHOT_DIR, filename), 'wb') as f:
        f.write(raw)
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('User not found')
    u['photo'] = f'/api/profile/photo/{uid}?t={int(time.time())}'
    save_data(data)
    return jsonify({'success': True, 'photo': u['photo']})

@app.route('/api/profile/photo/<uid>', methods=['GET'])
def get_profile_photo(uid):
    for ext in ['jpg', 'png', 'webp']:
        path = os.path.join(SHOT_DIR, f'profile_{uid}.{ext}')
        if os.path.exists(path):
            mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}[ext]
            with open(path, 'rb') as f:
                return Response(f.read(), mimetype=mime)
    return jsonify({'error': 'No photo'}), 404

# ============ ROOMS ============
@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    data = load_data()
    out, dirty, now = {}, False, time.time()
    category = request.args.get('category', '')
    for rid in list(data['rooms'].keys()):
        room = data['rooms'][rid]
        cleanup_room(room)
        if not room['members'] and now - room.get('active', now) > 600:
            data['rooms'].pop(rid)
            dirty = True
            continue
        room.setdefault('active', now)
        if category and room.get('category', '') != category:
            continue
        out[rid] = {
            'name': room['name'], 'creator': room['creator'],
            'creator_name': room.get('creator_name', ''),
            'count': len(room['members']),
            'category': room.get('category', 'chat'),
        }
    if dirty:
        save_data(data)
    return jsonify(out)

@app.route('/api/rooms/create', methods=['POST'])
def create_room():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    room_name = str(body.get('room_name', '')).strip()[:50]
    category = str(body.get('category', 'chat')).strip()[:20]
    if not room_name:
        return err('Room name required')
    # AUTO-GENERATE ROOM ID
    slug = re.sub(r'[^a-z0-9]+', '', room_name.lower())[:20] or 'room'
    room_id = f"{slug}_{uuid.uuid4().hex[:6]}"
    data = load_data()
    data['rooms'][room_id] = {
        'name': room_name, 'creator': uid,
        'creator_name': display_name(tg), 'created': now_iso(), 'active': time.time(),
        'seats': {}, 'members': {}, 'known': [],
        'messages': [], 'msg_seq': 0, 'category': category
    }
    save_data(data)
    return jsonify({'success': True, 'room_id': room_id})

@app.route('/api/rooms/<rid>/state', methods=['GET'])
def room_state(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'}), 404
    cleanup_room(room)
    known = room.setdefault('known', [])
    if uid not in known:
        known.append(uid)
    add_message(room, '', '👋 ' + display_name(tg) + ' joined', sys=True)
    room['members'][uid] = {'seen': time.time(), 'name': display_name(tg)}
    room['active'] = time.time()
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0
    msgs = [m for m in room['messages'] if m['id'] > since]
    save_data(data)
    u = data['users'].get(uid, {})
    return jsonify({
        'success': True, 'name': room['name'], 'host': room['creator'],
        'count': len(room['members']), 'seats': room['seats'],
        'messages': msgs, 'coins': u.get('coins', 0),
        'earnings': u.get('earnings', 0), 'category': room.get('category', 'chat'),
    })

@app.route('/api/rooms/<rid>/seat', methods=['POST'])
def take_seat(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        idx = int(body.get('seat'))
    except (TypeError, ValueError):
        return err('Invalid seat')
    if idx < 0 or idx >= MAX_SEATS:
        return err('Invalid seat')
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return err('Room not found')
    cleanup_room(room)
    if idx == 0 and room['creator'] != uid:
        return err('This is the host seat 👑')
    cur = room['seats'].get(str(idx))
    if cur and cur['uid'] != uid:
        return err('Seat already taken')
    remove_user_seats(room, uid)
    room['seats'][str(idx)] = {'uid': uid, 'name': display_name(tg), 'photo': photo_of(tg)}
    room['members'][uid] = {'seen': time.time(), 'name': display_name(tg)}
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/rooms/<rid>/exit', methods=['POST'])
def exit_room(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    room = data['rooms'].get(rid)
    if room:
        room.setdefault('seats', {})
        room.setdefault('members', {})
        remove_user_seats(room, uid)
        room['members'].pop(uid, None)
        known = room.setdefault('known', [])
        if uid in known:
            known.remove(uid)
        save_data(data)
    return jsonify({'success': True})

@app.route('/api/rooms/<rid>/chat', methods=['POST'])
def room_chat(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    text = str(body.get('text', '')).strip()[:200]
    if not text:
        return err('Empty')
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return err('Room not found')
    add_message(room, display_name(tg), text)
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/rooms/<rid>/gift', methods=['POST'])
def room_gift(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    gift_type = body.get('gift_type')
    to = body.get('recipient_id')
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return err('Room not found')
    cleanup_room(room)
    if not isinstance(to, str) or to == uid:
        return err('Invalid recipient')
    if to not in room['members'] or to not in data['users'] or uid not in data['users']:
        return err('Recipient not in room')
    gift = GIFTS.get(gift_type)
    if not gift:
        return err('Invalid gift')
    sender = data['users'][uid]
    if sender.get('coins', 0) < gift['price']:
        return jsonify({'success': False, 'error': 'Not enough coins', 'need_coins': True})
    if gift.get('luxury'):
        receiver_share = 0.70
    elif gift['rarity'] in ('legendary', 'mythic'):
        receiver_share = 0.04
    else:
        receiver_share = 0.02
    share = int(gift['price'] * receiver_share)
    fee = gift['price'] - share
    sender['coins'] -= gift['price']
    sender['total_gifts_sent'] = sender.get('total_gifts_sent', 0) + 1
    rec = data['users'][to]
    rec['earnings'] = rec.get('earnings', 0) + share
    rec['total_gifts_received'] = rec.get('total_gifts_received', 0) + 1
    data['meta']['fee_coins'] = data['meta'].get('fee_coins', 0) + fee
    data['meta']['gift_count'] = data['meta'].get('gift_count', 0) + 1
    data['transactions'].append({
        'from': uid, 'to': to, 'gift': gift_type, 'room': rid,
        'amount': gift['price'], 'share': share, 'fee': fee, 'timestamp': now_iso()
    })
    data['transactions'] = data['transactions'][-2000:]
    to_name = room['members'][to].get('name', '')
    sender_level = get_user_level(sender.get('total_gifts_sent', 0))
    rec_level = get_user_level(rec.get('total_gifts_received', 0))
    add_message(room, '', f'🎁 {display_name(tg)} [{sender_level["icon"]}] → {to_name} [{rec_level["icon"]}] {gift["icon"]} {gift["name"]}',
                sys=True, gift={'type': gift_type, 'icon': gift['icon'], 'to': to, 'price': gift['price'], 'rarity': gift['rarity']})
    save_data(data)
    return jsonify({'success': True, 'remaining_coins': sender['coins']})

# ============ WALLET ============
@app.route('/api/wallet', methods=['GET'])
def wallet():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('User not found')
    strip = lambda p: {k: v for k, v in p.items() if k != 'shot'}
    mine_p = [strip(p) for p in data['purchases'] if p['uid'] == uid][-10:][::-1]
    mine_w = [w for w in data['withdrawals'] if w['uid'] == uid][-10:][::-1]
    return jsonify({'success': True, 'user': user_view(u, data), 'purchases': mine_p, 'withdrawals': mine_w})

@app.route('/api/coins/buy', methods=['POST'])
def coins_buy():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    pkg = next((p for p in COIN_PACKAGES if p['id'] == body.get('package_id')), None)
    ref = str(body.get('ref', '')).strip().upper()
    if not pkg:
        return err('Package not selected')
    if not REF_RE.match(ref):
        return err('Invalid ref (8-20 chars)')
    data = load_data()
    if uid not in data['users']:
        return err('User not found')
    pending = [p for p in data['purchases'] if p['uid'] == uid and p['status'] == 'pending']
    if len(pending) >= 3:
        return err('Too many pending')
    if any(p['ref'].upper() == ref for p in data['purchases']):
        return err('Ref already used')
    pid = uuid.uuid4().hex[:10]
    shot = save_shot(pid, body.get('shot'))
    if not shot:
        return err('Attach screenshot')
    data['purchases'].append({
        'id': pid, 'uid': uid, 'name': display_name(tg),
        'coins': pkg['coins'], 'price': pkg['price'], 'ref': ref, 'shot': shot,
        'status': 'pending', 'created': now_iso()
    })
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    phone = norm_phone(body.get('phone'))
    acc_name = str(body.get('acc_name', '')).strip()[:60]
    try:
        amount = int(body.get('amount'))
    except (TypeError, ValueError):
        return err('Invalid amount')
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('User not found')
    if not phone:
        return err('Invalid Telebirr number')
    if not acc_name:
        return err('Enter name on Telebirr')
    if amount < MIN_WITHDRAW:
        return err('Minimum is ' + str(MIN_WITHDRAW))
    if amount > u.get('earnings', 0):
        return err('Not enough earnings')
    if len([w for w in data['withdrawals'] if w['uid'] == uid and w['status'] == 'pending']) >= 3:
        return err('Too many pending')
    u['earnings'] -= amount
    data['withdrawals'].append({
        'id': uuid.uuid4().hex[:10], 'uid': uid, 'name': display_name(tg),
        'amount': amount, 'birr': round(amount * WITHDRAW_RATE_ETB, 2),
        'phone': phone, 'acc_name': acc_name, 'status': 'pending', 'created': now_iso()
    })
    save_data(data)
    return jsonify({'success': True, 'earnings': u['earnings']})

# ============ COMMUNITY ============
@app.route('/api/posts', methods=['GET'])
def posts_list():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    return jsonify({'success': True, 'posts': data['posts'][::-1][:50]})

@app.route('/api/posts/create', methods=['POST'])
def posts_create():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    text = str(body.get('text', '')).strip()[:500]
    if not text:
        return err('Empty')
    data = load_data()
    data['posts'].append({
        'id': uuid.uuid4().hex[:8], 'uid': uid, 'name': display_name(tg),
        'text': text, 'likes': [], 'created': now_iso(), 'ts': time.time()
    })
    data['posts'] = data['posts'][-300:]
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/posts/<pid>/like', methods=['POST'])
def posts_like(pid):
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    p = next((x for x in data['posts'] if x['id'] == pid), None)
    if not p:
        return err('Not found')
    likes = p.setdefault('likes', [])
    if uid in likes:
        likes.remove(uid)
    else:
        likes.append(uid)
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/posts/<pid>/delete', methods=['POST'])
def posts_delete(pid):
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    p = next((x for x in data['posts'] if x['id'] == pid), None)
    if not p:
        return err('Not found')
    if p['uid'] != uid and not is_admin(uid):
        return forbidden()
    data['posts'].remove(p)
    save_data(data)
    return jsonify({'success': True})

# ============ JOBS ============
@app.route('/api/jobs', methods=['GET'])
def jobs_list():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    return jsonify({'success': True, 'jobs': data['jobs'][::-1][:60]})

@app.route('/api/jobs/create', methods=['POST'])
def jobs_create():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    title = str(body.get('title', '')).strip()[:80]
    desc = str(body.get('desc', '')).strip()[:500]
    contact = str(body.get('contact', '')).strip()[:100]
    if not title or not contact:
        return err('Title and contact required')
    data = load_data()
    if not is_admin(uid) and len([j for j in data['jobs'] if j['uid'] == uid]) >= 5:
        return err('Maximum 5 jobs')
    data['jobs'].append({
        'id': uuid.uuid4().hex[:8], 'uid': uid, 'name': display_name(tg),
        'title': title, 'desc': desc, 'contact': contact, 'created': now_iso()
    })
    data['jobs'] = data['jobs'][-200:]
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/jobs/<jid>/delete', methods=['POST'])
def jobs_delete(jid):
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    j = next((x for x in data['jobs'] if x['id'] == jid), None)
    if not j:
        return err('Not found')
    if j['uid'] != uid and not is_admin(uid):
        return forbidden()
    data['jobs'].remove(j)
    save_data(data)
    return jsonify({'success': True})

# ============ GAMES (70% RTP) ============
@app.route('/api/games', methods=['GET'])
def games_list():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    u = data['users'].get(uid, {})
    my_games = [g for g in data.get('games', []) if g['uid'] == uid][-30:][::-1]
    return jsonify({
        'success': True,
        'games_config': {k: {kk: vv for kk, vv in v.items() if kk not in ('segments', 'boxes')} for k, v in GAMES.items()},
        'my_games': my_games, 'user_coins': u.get('coins', 0),
        'house_stats': {'total_bet': data['meta'].get('game_total_bet', 0), 'house_profit': data['meta'].get('game_profit', 0), 'rtp': 70}
    })

@app.route('/api/games/dice', methods=['POST'])
def play_dice():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 0)); number = int(body.get('number', 0))
    except (TypeError, ValueError):
        return err('Invalid input')
    if number < 1 or number > 6:
        return err('Number 1-6')
    game = GAMES['dice']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    data['meta']['game_total_bet'] = data['meta'].get('game_total_bet', 0) + bet
    result = random.randint(1, 6)
    won = (result == number)
    payout = int(bet * game['multiplier']) if won else 0
    if won:
        u['coins'] += payout
    profit = bet - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + profit
    data['games'].append({'id': uuid.uuid4().hex[:8], 'uid': uid, 'game': 'dice', 'bet': bet, 'number': number, 'result': result, 'won': won, 'payout': payout, 'created': now_iso()})
    data['games'] = data['games'][-5000:]
    save_data(data)
    return jsonify({'success': True, 'won': won, 'result': result, 'payout': payout, 'remaining_coins': u['coins']})

@app.route('/api/games/flip', methods=['POST'])
def play_flip():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 0)); choice = str(body.get('choice', '')).lower()
    except (TypeError, ValueError):
        return err('Invalid input')
    if choice not in ('heads', 'tails'):
        return err('heads or tails')
    game = GAMES['flip']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    data['meta']['game_total_bet'] = data['meta'].get('game_total_bet', 0) + bet
    result = random.choice(['heads', 'tails'])
    won = (result == choice)
    payout = int(bet * game['multiplier']) if won else 0
    if won:
        u['coins'] += payout
    profit = bet - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + profit
    data['games'].append({'id': uuid.uuid4().hex[:8], 'uid': uid, 'game': 'flip', 'bet': bet, 'choice': choice, 'result': result, 'won': won, 'payout': payout, 'created': now_iso()})
    data['games'] = data['games'][-5000:]
    save_data(data)
    return jsonify({'success': True, 'won': won, 'result': result, 'payout': payout, 'remaining_coins': u['coins']})

@app.route('/api/games/wheel', methods=['POST'])
def play_wheel():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 0))
    except (TypeError, ValueError):
        return err('Invalid bet')
    game = GAMES['wheel']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    data['meta']['game_total_bet'] = data['meta'].get('game_total_bet', 0) + bet
    total_weight = sum(s['weight'] for s in game['segments'])
    r = random.randint(1, total_weight)
    cumulative = 0
    selected = game['segments'][0]
    for seg in game['segments']:
        cumulative += seg['weight']
        if r <= cumulative:
            selected = seg
            break
    multiplier = selected['mult']
    payout = int(bet * multiplier)
    if payout > 0:
        u['coins'] += payout
    profit = bet - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + profit
    data['games'].append({'id': uuid.uuid4().hex[:8], 'uid': uid, 'game': 'wheel', 'bet': bet, 'multiplier': multiplier, 'segment': selected['label'], 'won': multiplier > 0, 'payout': payout, 'created': now_iso()})
    data['games'] = data['games'][-5000:]
    save_data(data)
    return jsonify({'success': True, 'won': multiplier > 0, 'multiplier': multiplier, 'segment': selected['label'], 'payout': payout, 'remaining_coins': u['coins']})

@app.route('/api/games/race', methods=['POST'])
def play_race():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 0)); horse = int(body.get('horse', 0))
    except (TypeError, ValueError):
        return err('Invalid input')
    if horse < 1 or horse > 6:
        return err('Horse 1-6')
    game = GAMES['race']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    data['meta']['game_total_bet'] = data['meta'].get('game_total_bet', 0) + bet
    winner = random.randint(1, 6)
    won = (winner == horse)
    payout = int(bet * game['multiplier']) if won else 0
    if won:
        u['coins'] += payout
    profit = bet - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + profit
    data['games'].append({'id': uuid.uuid4().hex[:8], 'uid': uid, 'game': 'race', 'bet': bet, 'horse': horse, 'winner': winner, 'won': won, 'payout': payout, 'created': now_iso()})
    data['games'] = data['games'][-5000:]
    save_data(data)
    return jsonify({'success': True, 'won': won, 'winner': winner, 'payout': payout, 'remaining_coins': u['coins']})

@app.route('/api/games/box', methods=['POST'])
def play_box():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 0))
    except (TypeError, ValueError):
        return err('Invalid bet')
    game = GAMES['box']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    data['meta']['game_total_bet'] = data['meta'].get('game_total_bet', 0) + bet
    total_weight = sum(b['weight'] for b in game['boxes'])
    r = random.randint(1, total_weight)
    cumulative = 0
    selected = game['boxes'][0]
    for box in game['boxes']:
        cumulative += box['weight']
        if r <= cumulative:
            selected = box
            break
    multiplier = selected['mult']
    payout = int(bet * multiplier)
    if payout > 0:
        u['coins'] += payout
    profit = bet - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + profit
    data['games'].append({'id': uuid.uuid4().hex[:8], 'uid': uid, 'game': 'box', 'bet': bet, 'multiplier': multiplier, 'box': selected['label'], 'icon': selected['icon'], 'won': multiplier > 0, 'payout': payout, 'created': now_iso()})
    data['games'] = data['games'][-5000:]
    save_data(data)
    return jsonify({'success': True, 'won': multiplier > 0, 'multiplier': multiplier, 'box': selected['label'], 'icon': selected['icon'], 'payout': payout, 'remaining_coins': u['coins']})

@app.route('/api/games/p2p/create', methods=['POST'])
def p2p_create():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        bet = int(body.get('bet', 100))
    except (TypeError, ValueError):
        return err('Invalid bet')
    game = GAMES['p2p']
    if bet < game['min_bet'] or bet > game['max_bet']:
        return err(f'Bet {game["min_bet"]}-{game["max_bet"]}')
    data = load_data()
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < bet:
        return err('Not enough coins')
    u['coins'] -= bet
    game_id = uuid.uuid4().hex[:8]
    data['p2p_games'].append({'id': game_id, 'player1': uid, 'player1_name': display_name(None), 'player2': None, 'player2_name': None, 'bet': bet, 'status': 'waiting', 'created': now_iso()})
    data['p2p_games'] = data['p2p_games'][-1000:]
    save_data(data)
    return jsonify({'success': True, 'game_id': game_id})

@app.route('/api/games/p2p/join', methods=['POST'])
def p2p_join():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    game_id = str(body.get('game_id', ''))
    data = load_data()
    game = next((g for g in data['p2p_games'] if g['id'] == game_id), None)
    if not game:
        return err('Game not found')
    if game['status'] != 'waiting':
        return err('Game not available')
    if game['player1'] == uid:
        return err('Cannot play against yourself')
    u = data['users'].get(uid)
    if not u or u.get('coins', 0) < game['bet']:
        return err('Not enough coins')
    u['coins'] -= game['bet']
    game['player2'] = uid
    game['player2_name'] = display_name(None)
    game['status'] = 'playing'
    winner_is_p1 = random.random() < 0.5
    winner_uid = game['player1'] if winner_is_p1 else game['player2']
    payout = int(game['bet'] * 1.9)
    data['users'][winner_uid]['coins'] = data['users'][winner_uid].get('coins', 0) + payout
    house_fee = game['bet'] * 2 - payout
    data['meta']['game_profit'] = data['meta'].get('game_profit', 0) + int(house_fee)
    game['winner'] = winner_uid
    game['status'] = 'finished'
    game['payout'] = payout
    game['finished'] = now_iso()
    data['p2p_games'] = data['p2p_games'][-1000:]
    save_data(data)
    return jsonify({'success': True, 'won': winner_uid == uid, 'payout': payout if winner_uid == uid else 0, 'remaining_coins': data['users'][uid]['coins']})

# ============ AGENCY ============
@app.route('/api/agency', methods=['GET'])
def agency_info():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    info = calc_agency_commission(data, uid)
    referrer = data['users'].get(uid, {}).get('referrer', '')
    ref_info = None
    if referrer and referrer in data['users']:
        ref_u = data['users'][referrer]
        ref_info = {'uid': referrer, 'username': ref_u.get('username', ''), 'level': get_agency_level(ref_u.get('total_income', 0))}
    return jsonify({'success': True, 'agency': info, 'referrer': ref_info, 'levels': AGENCY_LEVELS})

@app.route('/api/agency/claim', methods=['POST'])
def agency_claim():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    info = calc_agency_commission(data, uid)
    if info['salary'] <= 0:
        return err('No commission to claim')
    u = data['users'][uid]
    u['coins'] = u.get('coins', 0) + info['salary']
    u['agency_claimed'] = u.get('agency_claimed', 0) + info['salary']
    data['agency_commissions'].append({'uid': uid, 'amount': info['salary'], 'level': info['level'], 'timestamp': now_iso()})
    save_data(data)
    return jsonify({'success': True, 'claimed': info['salary'], 'new_balance': u['coins']})

# ============ ADMIN ============
@app.route('/api/admin/overview', methods=['GET'])
@admin_only
def admin_overview(aid):
    data = load_data()
    users = data['users']
    strip = lambda p: {k: v for k, v in p.items() if k != 'shot'}
    approved = [p for p in data['purchases'] if p['status'] == 'approved']
    paid = [w for w in data['withdrawals'] if w['status'] == 'paid']
    rooms = []
    for rid, r in data['rooms'].items():
        cleanup_room(r)
        rooms.append({'id': rid, 'name': r['name'], 'creator_name': r.get('creator_name', ''), 'count': len(r['members']), 'category': r.get('category', 'chat')})
    return jsonify({
        'success': True,
        'stats': {
            'users': len(users), 'banned': sum(1 for u in users.values() if u.get('banned')),
            'rooms': len(rooms), 'posts': len(data['posts']), 'jobs': len(data['jobs']),
            'revenue': sum(p['price'] for p in approved), 'paid_out': round(sum(w['birr'] for w in paid), 2),
            'fee_coins': data['meta'].get('fee_coins', 0), 'gifts': data['meta'].get('gift_count', 0),
            'game_total_bet': data['meta'].get('game_total_bet', 0), 'game_profit': data['meta'].get('game_profit', 0),
        },
        'purchases': [strip(p) for p in data['purchases'] if p['status'] == 'pending'],
        'withdrawals': [w for w in data['withdrawals'] if w['status'] == 'pending'],
        'rooms': rooms,
    })

@app.route('/api/admin/users', methods=['GET'])
@admin_only
def admin_users(aid):
    q = request.args.get('q', '').strip().lower()
    data = load_data()
    rows = []
    for k, u in data['users'].items():
        if q and q not in k.lower() and q not in u.get('username', '').lower():
            continue
        agency = calc_agency_commission(data, k)
        rows.append({'uid': k, 'banned': bool(u.get('banned')), 'joined': u.get('joined', ''), 'username': u.get('username', ''), 'coins': u.get('coins', 0), 'earnings': u.get('earnings', 0), 'agency_level': agency['level'], 'agency_salary': agency['salary'], 'referrer': u.get('referrer', '')})
    rows.sort(key=lambda r: r['joined'], reverse=True)
    return jsonify({'success': True, 'users': rows[:50]})

@app.route('/api/admin/shot/<pid>', methods=['GET'])
@admin_only
def admin_shot(aid, pid):
    data = load_data()
    p = next((x for x in data['purchases'] if x['id'] == pid), None)
    if not p or not p.get('shot'):
        return err('Not found')
    path = os.path.join(SHOT_DIR, os.path.basename(p['shot']))
    if not os.path.exists(path):
        return err('Photo deleted')
    ext = path.rsplit('.', 1)[-1]
    mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    with open(path, 'rb') as f:
        return Response(f.read(), mimetype=mime)

@app.route('/api/admin/purchase', methods=['POST'])
@admin_only
def admin_purchase(aid):
    body = request.get_json(silent=True) or {}
    data = load_data()
    p = next((x for x in data['purchases'] if x['id'] == body.get('id')), None)
    if not p or p['status'] != 'pending':
        return err('Not found')
    if body.get('action') == 'approve':
        if p['uid'] in data['users']:
            data['users'][p['uid']]['coins'] = data['users'][p['uid']].get('coins', 0) + p['coins']
            referrer = data['users'][p['uid']].get('referrer', '')
            if referrer and referrer in data['users'] and referrer != p['uid']:
                bonus = int(p['coins'] * REFERRAL_BONUS_PERCENT / 100)
                data['users'][referrer]['coins'] = data['users'][referrer].get('coins', 0) + bonus
                data['users'][referrer]['referral_bonus'] = data['users'][referrer].get('referral_bonus', 0) + bonus
                data['users'][referrer]['referral_count'] = data['users'][referrer].get('referral_count', 0) + 1
        p['status'] = 'approved'
    else:
        p['status'] = 'rejected'
    p['handled'] = now_iso()
    p['by'] = aid
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/admin/withdraw', methods=['POST'])
@admin_only
def admin_withdraw(aid):
    body = request.get_json(silent=True) or {}
    data = load_data()
    w = next((x for x in data['withdrawals'] if x['id'] == body.get('id')), None)
    if not w or w['status'] != 'pending':
        return err('Not found')
    if body.get('action') == 'paid':
        w['status'] = 'paid'
    else:
        w['status'] = 'rejected'
        if w['uid'] in data['users']:
            data['users'][w['uid']]['earnings'] = data['users'][w['uid']].get('earnings', 0) + w['amount']
    w['handled'] = now_iso()
    w['by'] = aid
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/admin/user', methods=['POST'])
@admin_only
def admin_user_action(aid):
    body = request.get_json(silent=True) or {}
    target = str(body.get('uid', '')).strip()
    action = body.get('action')
    data = load_data()
    u = data['users'].get(target)
    if not u:
        return err('User not found')
    if is_admin(target) and action in ('ban', 'delete'):
        return err('Cannot ban admin')
    if action == 'ban':
        u['banned'] = True
        purge_user_from_rooms(data, target)
    elif action == 'unban':
        u['banned'] = False
    elif action == 'delete':
        data['users'].pop(target)
        purge_user_from_rooms(data, target)
        data['posts'] = [p for p in data['posts'] if p['uid'] != target]
        data['jobs'] = [j for j in data['jobs'] if j['uid'] != target]
        for p in data['purchases']:
            if p['uid'] == target and p['status'] == 'pending':
                p['status'] = 'rejected'
                p['handled'] = now_iso()
        for w in data['withdrawals']:
            if w['uid'] == target and w['status'] == 'pending':
                w['status'] = 'rejected'
                w['handled'] = now_iso()
    elif action == 'grant':
        try:
            amount = int(body.get('amount'))
        except (TypeError, ValueError):
            return err('Invalid amount')
        if abs(amount) > 10000000:
            return err('Amount too large')
        u['coins'] = max(0, u.get('coins', 0) + amount)
    else:
        return err('Invalid action')
    save_data(data)
    return jsonify({'success': True, 'coins': u.get('coins', 0)})

@app.route('/api/admin/room', methods=['POST'])
@admin_only
def admin_room(aid):
    body = request.get_json(silent=True) or {}
    data = load_data()
    if data['rooms'].pop(str(body.get('id', '')), None) is None:
        return err('Not found')
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/livekit-token', methods=['POST'])
def livekit_token():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    room_id = str(body.get('room_id', ''))
    data = load_data()
    room = data['rooms'].get(room_id)
    if not room:
        return err('Room not found')
    try:
        token = (
            lkapi.AccessToken(os.environ.get('LIVEKIT_API_KEY', ''), os.environ.get('LIVEKIT_API_SECRET', ''))
            .with_identity(uid)
            .with_name(display_name(tg))
            .with_grants(lkapi.VideoGrants(room_join=True, room=room_id, can_publish=True, can_subscribe=True, can_publish_data=True))
            .to_jwt()
        )
    except Exception as e:
        return jsonify({'success': False, 'error': 'LiveKit not configured: ' + str(e)})
    return jsonify({'success': True, 'token': token, 'url': os.environ.get('LIVEKIT_URL', ''), 'is_host': room['creator'] == uid})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
