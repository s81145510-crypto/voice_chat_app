from flask import Flask, render_template, request, jsonify
import json, os, re, hmac, hashlib, time, uuid
from urllib.parse import parse_qsl
from datetime import datetime
from livekit import api as lkapi

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'db.json')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
ADMIN_IDS = {x.strip() for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()}
PAY_INFO = os.environ.get('PAY_INFO', 'የክፍያ መረጃ ገና አልተሞላም')

MAX_SEATS = 9
STALE_SECONDS = 45
WELCOME_COINS = 100
RECEIVER_SHARE = 0.7          # ተቀባይ የሚያገኘው ድርሻ
WITHDRAW_RATE_ETB = 0.4       # 1 ገቢ ሳንቲም = ስንት ብር
MIN_WITHDRAW = 500            # ዝቅተኛ የማውጫ መጠን (ሳንቲም)
ID_RE = re.compile(r'^[A-Za-z0-9_]{1,40}$')

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


def current_user_id():
    tg = verify_init_data(request.headers.get('X-Telegram-Init-Data', ''))
    return (f"tg_{tg['id']}", tg) if tg else (None, None)


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
    for k, v in (('users', {}), ('rooms', {}), ('transactions', []),
                 ('purchases', []), ('withdrawals', [])):
        data.setdefault(k, v)
    data['gifts'] = GIFTS
    return data


def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, DATA_FILE)


def unauthorized():
    return jsonify({'success': False, 'error': 'Unauthorized'}), 401


def forbidden():
    return jsonify({'success': False, 'error': 'Forbidden'}), 403


def display_name(tg):
    return (tg.get('first_name') or 'User')[:30]


def photo_of(tg):
    url = tg.get('photo_url') or ''
    return url if url.startswith('https://') else ''


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


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


def remove_user_seats(room, uid):
    for k in [k for k, s in room['seats'].items() if s['uid'] == uid]:
        room['seats'].pop(k)


def user_view(u):
    return {'username': u.get('username', ''), 'coins': u.get('coins', 0),
            'earnings': u.get('earnings', 0)}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/register', methods=['POST'])
def register():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    if uid not in data['users']:
        data['users'][uid] = {
            'username': display_name(tg), 'coins': WELCOME_COINS,
            'earnings': 0, 'joined': now_iso()
        }
        save_data(data)
    u = data['users'][uid]
    gifts = [dict(id=k, **v) for k, v in GIFTS.items()]
    return jsonify({
        'success': True, 'user_id': uid, 'user': user_view(u),
        'admin': is_admin(uid),
        'config': {
            'gifts': gifts, 'packages': COIN_PACKAGES, 'pay_info': PAY_INFO,
            'rate': WITHDRAW_RATE_ETB, 'min_withdraw': MIN_WITHDRAW
        }
    })


@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    data = load_data()
    out = {}
    for rid, room in data['rooms'].items():
        cleanup_room(room)
        out[rid] = {
            'name': room['name'], 'creator': room['creator'],
            'creator_name': room.get('creator_name', ''),
            'count': len(room['members'])
        }
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
        return jsonify({'success': False, 'error': 'Invalid room'})
    data = load_data()
    data['rooms'][room_id] = {
        'name': room_name, 'creator': uid,
        'creator_name': display_name(tg), 'created': now_iso(),
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
        return jsonify({'success': False, 'error': 'Invalid seat'})
    if idx < 0 or idx >= MAX_SEATS:
        return jsonify({'success': False, 'error': 'Invalid seat'})
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'})
    cleanup_room(room)
    if idx == 0 and room['creator'] != uid:
        return jsonify({'success': False, 'error': 'ይህ የhost መቀመጫ ነው'})
    cur = room['seats'].get(str(idx))
    if cur and cur['uid'] != uid:
        return jsonify({'success': False, 'error': 'መቀመጫው ተይዟል'})
    remove_user_seats(room, uid)
    room['seats'][str(idx)] = {
        'uid': uid, 'name': display_name(tg), 'photo': photo_of(tg)
    }
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
        return jsonify({'success': False, 'error': 'Room not found'})
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
        return jsonify({'success': False, 'error': 'Empty'})
    data = load_data()
    room = data['rooms'].get(rid)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'})
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
        return jsonify({'success': False, 'error': 'Room not found'})
    cleanup_room(room)
    if not isinstance(to, str) or to == uid:
        return jsonify({'success': False, 'error': 'Invalid recipient'})
    if to not in room['members'] or to not in data['users'] or uid not in data['users']:
        return jsonify({'success': False, 'error': 'ተቀባዩ በክፍሉ የለም'})
    gift = GIFTS.get(gift_type)
    if not gift:
        return jsonify({'success': False, 'error': 'Invalid gift'})
    sender = data['users'][uid]
    if sender.get('coins', 0) < gift['price']:
        return jsonify({'success': False, 'error': 'ሳንቲም አይበቃም', 'need_coins': True})
    sender['coins'] -= gift['price']
    rec = data['users'][to]
    rec['earnings'] = rec.get('earnings', 0) + int(gift['price'] * RECEIVER_SHARE)
    data['transactions'].append({
        'from': uid, 'to': to, 'gift': gift_type, 'room': rid,
        'amount': gift['price'], 'timestamp': now_iso()
    })
    data['transactions'] = data['transactions'][-2000:]
    to_name = room['members'][to].get('name', '')
    add_message(
        room, '', '🎁 ' + display_name(tg) + ' → ' + to_name + ' ' + gift['icon'] + ' ' + gift['name'],
        sys=True,
        gift={'type': gift_type, 'icon': gift['icon'], 'to': to, 'price': gift['price']})
    save_data(data)
    return jsonify({'success': True, 'remaining_coins': sender['coins']})


@app.route('/api/wallet', methods=['GET'])
def wallet():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return jsonify({'success': False, 'error': 'User not found'})
    mine_p = [p for p in data['purchases'] if p['uid'] == uid][-10:][::-1]
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
    ref = str(body.get('ref', '')).strip()[:100]
    if not pkg or not ref:
        return jsonify({'success': False, 'error': 'የደረሰኝ ቁጥር አስገባ'})
    data = load_data()
    if uid not in data['users']:
        return jsonify({'success': False, 'error': 'User not found'})
    pending = [p for p in data['purchases'] if p['uid'] == uid and p['status'] == 'pending']
    if len(pending) >= 3:
        return jsonify({'success': False, 'error': 'ብዙ ጥያቄዎች በመጠባበቅ ላይ ናቸው'})
    if any(p['ref'].lower() == ref.lower() for p in data['purchases']):
        return jsonify({'success': False, 'error': 'ይህ የደረሰኝ ቁጥር ቀድሞ ገብቷል'})
    data['purchases'].append({
        'id': uuid.uuid4().hex[:10], 'uid': uid, 'name': display_name(tg),
        'coins': pkg['coins'], 'price': pkg['price'], 'ref': ref,
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
    account = str(body.get('account', '')).strip()[:120]
    try:
        amount = int(body.get('amount'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'መጠን ስህተት ነው'})
    data = load_data()
    u = data['users'].get(uid)
    if not u:
        return jsonify({'success': False, 'error': 'User not found'})
    if not account:
        return jsonify({'success': False, 'error': 'የሂሳብ ቁጥር አስገባ'})
    if amount < MIN_WITHDRAW:
        return jsonify({'success': False, 'error': 'ዝቅተኛው ' + str(MIN_WITHDRAW) + ' ነው'})
    if amount > u.get('earnings', 0):
        return jsonify({'success': False, 'error': 'ገቢህ አይበቃም'})
    u['earnings'] -= amount
    data['withdrawals'].append({
        'id': uuid.uuid4().hex[:10], 'uid': uid, 'name': display_name(tg),
        'amount': amount, 'birr': round(amount * WITHDRAW_RATE_ETB, 2),
        'account': account, 'status': 'pending', 'created': now_iso()
    })
    save_data(data)
    return jsonify({'success': True, 'earnings': u['earnings']})


# ---------------- አድሚን ----------------
@app.route('/api/admin/overview', methods=['GET'])
def admin_overview():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    if not is_admin(uid):
        return forbidden()
    data = load_data()
    users = data['users']
    revenue = sum(p['price'] for p in data['purchases'] if p['status'] == 'approved')
    top = sorted(users.items(), key=lambda kv: kv[1].get('joined', ''), reverse=True)[:40]
    return jsonify({
        'success': True,
        'stats': {
            'users': len(users), 'rooms': len(data['rooms']),
            'coins': sum(u.get('coins', 0) for u in users.values()),
            'revenue': revenue,
            'gifts': len(data['transactions'])
        },
        'purchases': [p for p in data['purchases'] if p['status'] == 'pending'],
        'withdrawals': [w for w in data['withdrawals'] if w['status'] == 'pending'],
        'users': [dict(uid=k, **user_view(v)) for k, v in top]
    })


@app.route('/api/admin/purchase', methods=['POST'])
def admin_purchase():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    if not is_admin(uid):
        return forbidden()
    body = request.get_json(silent=True) or {}
    data = load_data()
    p = next((x for x in data['purchases'] if x['id'] == body.get('id')), None)
    if not p or p['status'] != 'pending':
        return jsonify({'success': False, 'error': 'አልተገኘም'})
    if body.get('action') == 'approve':
        if p['uid'] in data['users']:
            data['users'][p['uid']]['coins'] = data['users'][p['uid']].get('coins', 0) + p['coins']
        p['status'] = 'approved'
    else:
        p['status'] = 'rejected'
    p['handled'] = now_iso()
    save_data(data)
    return jsonify({'success': True})


@app.route('/api/admin/withdraw', methods=['POST'])
def admin_withdraw():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    if not is_admin(uid):
        return forbidden()
    body = request.get_json(silent=True) or {}
    data = load_data()
    w = next((x for x in data['withdrawals'] if x['id'] == body.get('id')), None)
    if not w or w['status'] != 'pending':
        return jsonify({'success': False, 'error': 'አልተገኘም'})
    if body.get('action') == 'paid':
        w['status'] = 'paid'
    else:
        w['status'] = 'rejected'
        if w['uid'] in data['users']:
            data['users'][w['uid']]['earnings'] = data['users'][w['uid']].get('earnings', 0) + w['amount']
    w['handled'] = now_iso()
    save_data(data)
    return jsonify({'success': True})


@app.route('/api/admin/grant', methods=['POST'])
def admin_grant():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    if not is_admin(uid):
        return forbidden()
    body = request.get_json(silent=True) or {}
    target = str(body.get('uid', '')).strip()
    try:
        amount = int(body.get('amount'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'መጠን ስህተት ነው'})
    if abs(amount) > 10000000:
        return jsonify({'success': False, 'error': 'መጠኑ በጣም ትልቅ ነው'})
    data = load_data()
    u = data['users'].get(target)
    if not u:
        return jsonify({'success': False, 'error': 'ተጠቃሚ አልተገኘም'})
    u['coins'] = max(0, u.get('coins', 0) + amount)
    save_data(data)
    return jsonify({'success': True, 'coins': u['coins']})


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
        return jsonify({'success': False, 'error': 'Room not found'})
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
