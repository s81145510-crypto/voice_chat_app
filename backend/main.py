from flask import Flask, render_template, request, jsonify, Response, g
import json, os, re, hmac, hashlib, time, uuid, base64
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

ID_RE = re.compile(r'^[A-Za-z0-9_]{1,40}$')
REF_RE = re.compile(r'^[A-Za-z0-9]{8,20}$')

GIFTS = {
    'heart':    {'name': 'ልብ',        'price': 5,     'icon': '❤️'},
    'coffee':   {'name': 'ቡና',        'price': 10,    'icon': '☕'},
    'candy':    {'name': 'ከረሜላ',     'price': 20,    'icon': '🍭'},
    'rose':     {'name': 'ሮዝ',        'price': 50,    'icon': '🌹'},
    'choco':    {'name': 'ቸኮሌት',     'price': 80,    'icon': '🍫'},
    'bear':     {'name': 'ድብ',        'price': 100,   'icon': '🧸'},
    'cake':     {'name': 'ኬክ',        'price': 200,   'icon': '🎂'},
    'champ':    {'name': 'ሻምፓኝ',     'price': 300,   'icon': '🍾'},
    'diamond':  {'name': 'አልማዝ',      'price': 500,   'icon': '💎'},
    'crown':    {'name': 'ዘውድ',       'price': 800,   'icon': '👑'},
    'car':      {'name': 'መኪና',       'price': 1000,  'icon': '🚗'},
    'ring':     {'name': 'ቀለበት',      'price': 1500,  'icon': '💍'},
    'watch':    {'name': 'ሰዓት',       'price': 2000,  'icon': '⌚'},
    'bike':     {'name': 'ሞተር',       'price': 3000,  'icon': '🏍️'},
    'boat':     {'name': 'ጀልባ',       'price': 4000,  'icon': '🛥️'},
    'castle':   {'name': 'ቤተመንግስት',  'price': 5000,  'icon': '🏰'},
    'plane':    {'name': 'አውሮፕላን',   'price': 7500,  'icon': '✈️'},
    'rocket':   {'name': 'ሮኬት',       'price': 10000, 'icon': '🚀'},
    'island':   {'name': 'ደሴት',       'price': 20000, 'icon': '🏝️'},
    'universe': {'name': 'ዩኒቨርስ',     'price': 50000, 'icon': '🌌'},
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

# ---------------- መሠረታዊ ----------------
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
                 ('withdrawals', []), ('posts', []), ('jobs', []),
                 ('meta', {'fee_coins': 0, 'gift_count': 0})):
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

def user_view(u):
    return {'username': u.get('username', ''), 'coins': u.get('coins', 0),
            'earnings': u.get('earnings', 0), 'bio': u.get('bio', ''),
            'phone': u.get('phone', ''), 'joined': u.get('joined', ''),
            'photo': u.get('photo', '')}

# ---------------- መግቢያ / ፕሮፋይል ----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    changed = False
    if uid not in data['users']:
        data['users'][uid] = {
            'username': display_name(tg), 'coins': WELCOME_COINS,
            'earnings': 0, 'joined': now_iso(), 'bio': '', 'phone': ''
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
    return jsonify({
        'success': True, 'user_id': uid, 'user': user_view(u),
        'admin': is_admin(uid),
        'config': {
            'gifts': gifts, 'packages': COIN_PACKAGES,
            'telebirr': {'number': TELEBIRR_NUMBER, 'name': TELEBIRR_NAME},
            'rate': WITHDRAW_RATE_ETB, 'min_withdraw': MIN_WITHDRAW,
            'fee_percent': int(round((1 - RECEIVER_SHARE) * 100)),
            'seats': MAX_SEATS
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
            return err('የTelebirr ቁጥር ስህተት ነው (09xxxxxxxx)')
        u['phone'] = p
    else:
        u['phone'] = ''
    save_data(data)
    return jsonify({'success': True, 'user': user_view(u)})

@app.route('/api/profile/photo', methods=['POST'])
def profile_photo():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    photo_data = body.get('photo')
    if not photo_data:
        return err('ፎቶ የለም')
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.+)$', str(photo_data), re.S)
    if not m:
        return err('ስህተት ያለበት ፎቶ')
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return err('ፎቶ መፍታት አልተቻለም')
    if len(raw) < 500 or len(raw) > 2_500_000:
        return err('ፎቶው በጣም ትልቅ ወይም ትንሽ ነው')
    if raw[:2] == b'\xff\xd8':
        ext = 'jpg'
    elif raw[:4] == b'\x89PNG':
        ext = 'png'
    elif raw[:4] == b'RIFF':
        ext = 'webp'
    else:
        return err('ያልተፈቀደ የፎቶ አይነት')
    os.makedirs(SHOT_DIR, exist_ok=True)
    filename = f'profile_{uid}.{ext}'
    with open(os.path.join(SHOT_DIR, filename), 'wb') as f:
        f.write(raw)
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('ተጠቃሚ አልተገኘም')
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
    return jsonify({'error': 'ፎቶ የለም'}), 404

# ---------------- ክፍሎች ----------------
@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    data = load_data()
    out, dirty, now = {}, False, time.time()
    for rid in list(data['rooms'].keys()):
        room = data['rooms'][rid]
        cleanup_room(room)
        if not room['members'] and now - room.get('active', now) > 600:
            data['rooms'].pop(rid)
            dirty = True
            continue
        room.setdefault('active', now)
        out[rid] = {
            'name': room['name'], 'creator': room['creator'],
            'creator_name': room.get('creator_name', ''),
            'count': len(room['members'])
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
    room_id = str(body.get('room_id', ''))
    room_name = str(body.get('room_name', '')).strip()[:50]
    if not ID_RE.match(room_id) or not room_name:
        return err('Invalid room')
    data = load_data()
    data['rooms'][room_id] = {
        'name': room_name, 'creator': uid,
        'creator_name': display_name(tg), 'created': now_iso(), 'active': time.time(),
        'seats': {}, 'members': {}, 'known': [],
        'messages': [], 'msg_seq': 0
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
    add_message(room, '', '👋 ' + display_name(tg) + ' ገባ', sys=True)
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
        'earnings': u.get('earnings', 0)
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
        return err('ይህ የባለቤቱ ዙፋን ነው 👑')
    cur = room['seats'].get(str(idx))
    if cur and cur['uid'] != uid:
        return err('መቀመጫው ተይዟል')
    remove_user_seats(room, uid)
    room['seats'][str(idx)] = {'uid': uid, 'name': display_name(tg), 'photo': photo_of(tg)}
    room['members'][uid] = {'seen': time.time(), 'name': display_name(tg)}
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/rooms/<rid>/leave-seat', methods=['POST'])
def leave_seat(rid):
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return err('Room not found')
    room.setdefault('seats', {})
    remove_user_seats(room, uid)
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
        return err('ተቀባዩ በክፍሉ የለም')
    gift = GIFTS.get(gift_type)
    if not gift:
        return err('Invalid gift')
    sender = data['users'][uid]
    if sender.get('coins', 0) < gift['price']:
        return jsonify({'success': False, 'error': 'ሳንቲም አይበቃም', 'need_coins': True})
    share = int(gift['price'] * RECEIVER_SHARE)
    fee = gift['price'] - share
    sender['coins'] -= gift['price']
    rec = data['users'][to]
    rec['earnings'] = rec.get('earnings', 0) + share
    data['meta']['fee_coins'] = data['meta'].get('fee_coins', 0) + fee
    data['meta']['gift_count'] = data['meta'].get('gift_count', 0) + 1
    data['transactions'].append({
        'from': uid, 'to': to, 'gift': gift_type, 'room': rid,
        'amount': gift['price'], 'share': share, 'fee': fee, 'timestamp': now_iso()
    })
    data['transactions'] = data['transactions'][-2000:]
    to_name = room['members'][to].get('name', '')
    add_message(
        room, '', '🎁 ' + display_name(tg) + ' → ' + to_name + ' ' + gift['icon'] + ' ' + gift['name'],
        sys=True,
        gift={'type': gift_type, 'icon': gift['icon'], 'to': to, 'price': gift['price']})
    save_data(data)
    return jsonify({'success': True, 'remaining_coins': sender['coins']})

# ---------------- ዋሌት ----------------
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
    return jsonify({'success': True, 'user': user_view(u),
                    'purchases': mine_p, 'withdrawals': mine_w})

@app.route('/api/coins/buy', methods=['POST'])
def coins_buy():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    pkg = next((p for p in COIN_PACKAGES if p['id'] == body.get('package_id')), None)
    ref = str(body.get('ref', '')).strip().upper()
    if not pkg:
        return err('ጥቅል አልተመረጠም')
    if not REF_RE.match(ref):
        return err('የTelebirr ግብይት ቁጥር ስህተት ነው (8-20 ፊደል/ቁጥር)')
    data = load_data()
    if uid not in data['users']:
        return err('User not found')
    pending = [p for p in data['purchases'] if p['uid'] == uid and p['status'] == 'pending']
    if len(pending) >= 3:
        return err('ብዙ ጥያቄዎች በመጠባበቅ ላይ ናቸው')
    if any(p['ref'].upper() == ref for p in data['purchases']):
        return err('ይህ የግብይት ቁጥር ቀድሞ ገብቷል')
    pid = uuid.uuid4().hex[:10]
    shot = save_shot(pid, body.get('shot'))
    if not shot:
        return err('የክፍያ ስክሪንሾት ፎቶ ያያይዙ')
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
        return err('መጠን ስህተት ነው')
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return err('User not found')
    if not phone:
        return err('ትክክለኛ የTelebirr ቁጥር አስገባ (09xxxxxxxx)')
    if not acc_name:
        return err('በTelebirr ላይ ያለውን ስም አስገባ')
    if amount < MIN_WITHDRAW:
        return err('ዝቅተኛው ' + str(MIN_WITHDRAW) + ' ነው')
    if amount > u.get('earnings', 0):
        return err('ገቢህ አይበቃም')
    if len([w for w in data['withdrawals'] if w['uid'] == uid and w['status'] == 'pending']) >= 3:
        return err('ብዙ ጥያቄዎች በመጠባበቅ ላይ ናቸው')
    u['earnings'] -= amount
    data['withdrawals'].append({
        'id': uuid.uuid4().hex[:10], 'uid': uid, 'name': display_name(tg),
        'amount': amount, 'birr': round(amount * WITHDRAW_RATE_ETB, 2),
        'phone': phone, 'acc_name': acc_name, 'status': 'pending', 'created': now_iso()
    })
    save_data(data)
    return jsonify({'success': True, 'earnings': u['earnings']})

# ---------------- ማኅበረሰብ ----------------
def post_view(p, uid):
    likes = p.get('likes', [])
    return {'id': p['id'], 'name': p['name'], 'photo': p.get('photo', ''), 'text': p['text'],
            'created': p['created'], 'likes': len(likes), 'liked': uid in likes,
            'mine': p['uid'] == uid, 'photo_url': p.get('photo_url', '')}

@app.route('/api/posts', methods=['GET'])
def posts_list():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    return jsonify({'success': True,
                    'posts': [post_view(p, uid) for p in data['posts'][::-1][:50]]})

@app.route('/api/posts/create', methods=['POST'])
def posts_create():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    text = str(body.get('text', '')).strip()[:500]
    photo_data = body.get('photo')
    if not text and not photo_data:
        return err('ባዶ ነው')
    data = load_data()
    mine = [p for p in data['posts'] if p['uid'] == uid]
    if mine and time.time() - mine[-1].get('ts', 0) < 8:
        return err('ትንሽ ቆይተህ ለጥፍ')
    photo_url = ''
    if photo_data:
        pid = uuid.uuid4().hex[:8]
        shot = save_shot(pid, photo_data)
        if shot:
            photo_url = f'/api/posts/photo/{pid}'
    data['posts'].append({
        'id': uuid.uuid4().hex[:8], 'uid': uid, 'name': display_name(tg),
        'photo': photo_of(tg), 'text': text, 'photo_url': photo_url,
        'likes': [], 'created': now_iso(), 'ts': time.time()
    })
    data['posts'] = data['posts'][-300:]
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/posts/photo/<pid>', methods=['GET'])
def get_post_photo(pid):
    for ext in ['jpg', 'png', 'webp']:
        path = os.path.join(SHOT_DIR, f'{pid}.{ext}')
        if os.path.exists(path):
            mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}[ext]
            with open(path, 'rb') as f:
                return Response(f.read(), mimetype=mime)
    return jsonify({'error': 'ፎቶ የለም'}), 404

@app.route('/api/posts/<pid>/like', methods=['POST'])
def posts_like(pid):
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    p = next((x for x in data['posts'] if x['id'] == pid), None)
    if not p:
        return err('አልተገኘም')
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
        return err('አልተገኘም')
    if p['uid'] != uid and not is_admin(uid):
        return forbidden()
    data['posts'].remove(p)
    save_data(data)
    return jsonify({'success': True})

# ---------------- ሥራ ----------------
def job_view(j, uid):
    return {'id': j['id'], 'title': j['title'], 'desc': j['desc'], 'contact': j['contact'],
            'name': j['name'], 'created': j['created'], 'mine': j['uid'] == uid,
            'photo_url': j.get('photo_url', '')}

@app.route('/api/jobs', methods=['GET'])
def jobs_list():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    return jsonify({'success': True, 'jobs': [job_view(j, uid) for j in data['jobs'][::-1][:60]]})

@app.route('/api/jobs/create', methods=['POST'])
def jobs_create():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    title = str(body.get('title', '')).strip()[:80]
    desc = str(body.get('desc', '')).strip()[:500]
    contact = str(body.get('contact', '')).strip()[:100]
    photo_data = body.get('photo')
    if not title or not contact:
        return err('ርዕስና ማግኛ ያስፈልጋል')
    data = load_data()
    if not is_admin(uid) and len([j for j in data['jobs'] if j['uid'] == uid]) >= 5:
        return err('ቢበዛ 5 ሥራ መለጠፍ ይቻላል')
    photo_url = ''
    if photo_data:
        jid = uuid.uuid4().hex[:8]
        shot = save_shot(jid, photo_data)
        if shot:
            photo_url = f'/api/jobs/photo/{jid}'
    data['jobs'].append({
        'id': uuid.uuid4().hex[:8], 'uid': uid, 'name': display_name(tg),
        'title': title, 'desc': desc, 'contact': contact,
        'photo_url': photo_url, 'created': now_iso()
    })
    data['jobs'] = data['jobs'][-200:]
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/jobs/photo/<jid>', methods=['GET'])
def get_job_photo(jid):
    for ext in ['jpg', 'png', 'webp']:
        path = os.path.join(SHOT_DIR, f'{jid}.{ext}')
        if os.path.exists(path):
            mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}[ext]
            with open(path, 'rb') as f:
                return Response(f.read(), mimetype=mime)
    return jsonify({'error': 'ፎቶ የለም'}), 404

@app.route('/api/jobs/<jid>/delete', methods=['POST'])
def jobs_delete(jid):
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    j = next((x for x in data['jobs'] if x['id'] == jid), None)
    if not j:
        return err('አልተገኘም')
    if j['uid'] != uid and not is_admin(uid):
        return forbidden()
    data['jobs'].remove(j)
    save_data(data)
    return jsonify({'success': True})

# ---------------- ሱፐር አድሚን ----------------
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
        rooms.append({'id': rid, 'name': r['name'], 'creator_name': r.get('creator_name', ''),
                      'count': len(r['members'])})
    handled_p = sorted([strip(p) for p in data['purchases'] if p['status'] != 'pending'],
                       key=lambda x: x.get('handled', ''), reverse=True)[:15]
    handled_w = sorted([w for w in data['withdrawals'] if w['status'] != 'pending'],
                       key=lambda x: x.get('handled', ''), reverse=True)[:15]
    return jsonify({
        'success': True,
        'stats': {
            'users': len(users), 'banned': sum(1 for u in users.values() if u.get('banned')),
            'rooms': len(rooms), 'posts': len(data['posts']), 'jobs': len(data['jobs']),
            'coins': sum(u.get('coins', 0) for u in users.values()),
            'earnings': sum(u.get('earnings', 0) for u in users.values()),
            'revenue': sum(p['price'] for p in approved),
            'paid_out': round(sum(w['birr'] for w in paid), 2),
            'fee_coins': data['meta'].get('fee_coins', 0),
            'gifts': data['meta'].get('gift_count', 0)
        },
        'purchases': [strip(p) for p in data['purchases'] if p['status'] == 'pending'],
        'withdrawals': [w for w in data['withdrawals'] if w['status'] == 'pending'],
        'rooms': rooms, 'history_p': handled_p, 'history_w': handled_w
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
        rows.append(dict(uid=k, banned=bool(u.get('banned')), joined=u.get('joined', ''),
                         **user_view(u)))
    rows.sort(key=lambda r: r['joined'], reverse=True)
    return jsonify({'success': True, 'users': rows[:40]})

@app.route('/api/admin/shot/<pid>', methods=['GET'])
@admin_only
def admin_shot(aid, pid):
    data = load_data()
    p = next((x for x in data['purchases'] if x['id'] == pid), None)
    if not p or not p.get('shot'):
        return err('አልተገኘም')
    path = os.path.join(SHOT_DIR, os.path.basename(p['shot']))
    if not os.path.exists(path):
        return err('ፎቶው ጠፍቷል')
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
        return err('አልተገኘም')
    if body.get('action') == 'approve':
        if p['uid'] in data['users']:
            data['users'][p['uid']]['coins'] = data['users'][p['uid']].get('coins', 0) + p['coins']
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
        return err('አልተገኘም')
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
        return err('ተጠቃሚ አልተገኘም')
    if is_admin(target) and action in ('ban', 'delete'):
        return err('አድሚንን ማገድ ወይም መሰረዝ አይቻልም')
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
            return err('መጠን ስህተት ነው')
        if abs(amount) > 10000000:
            return err('መጠኑ በጣም ትልቅ ነው')
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
        return err('አልተገኘም')
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
    token = (
        lkapi.AccessToken(
            os.environ.get('LIVEKIT_API_KEY', ''),
            os.environ.get('LIVEKIT_API_SECRET', ''))
        .with_identity(uid)
        .with_name(display_name(tg))
        .with_grants(lkapi.VideoGrants(
            room_join=True, room=room_id,
            can_publish=True, can_subscribe=True, can_publish_data=True))
        .to_jwt()
    )
    return jsonify({
        'success': True, 'token': token,
        'url': os.environ.get('LIVEKIT_URL', ''),
        'is_host': room['creator'] == uid
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
