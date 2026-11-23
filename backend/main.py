from flask import Flask, render_template, request, jsonify
import json, os, re, hmac, hashlib, time
from urllib.parse import parse_qsl
from datetime import datetime
from livekit import api as lkapi

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'db.json')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

MAX_SEATS = 9
STALE_SECONDS = 20
ID_RE = re.compile(r'^[A-Za-z0-9_]{1,40}$')


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


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {
        'users': {}, 'rooms': {},
        'gifts': {
            'coffee': {'name': 'Coffee', 'price': 10, 'icon': '☕'},
            'rose': {'name': 'Rose', 'price': 50, 'icon': '🌹'},
            'car': {'name': 'Car', 'price': 1000, 'icon': '🚗'}
        },
        'transactions': []
    }


def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def unauthorized():
    return jsonify({'success': False, 'error': 'Unauthorized'}), 401


def display_name(tg):
    return (tg.get('first_name') or 'User')[:30]


def photo_of(tg):
    url = tg.get('photo_url') or ''
    return url if url.startswith('https://') else ''


def add_message(room, name, text, sys=False):
    room['msg_seq'] = room.get('msg_seq', 0) + 1
    room.setdefault('messages', []).append({
        'id': room['msg_seq'], 'name': name, 'text': text, 'sys': sys
    })
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
            'username': display_name(tg),
            'coins': 100,
            'joined': datetime.now().isoformat()
        }
        save_data(data)
    return jsonify({'success': True, 'user_id': uid, 'user': data['users'][uid]})


@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    data = load_data()
    out = {}
    for rid, room in data['rooms'].items():
        cleanup_room(room)
        out[rid] = {
            'name': room['name'],
            'creator': room['creator'],
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
        'creator_name': display_name(tg),
        'created': datetime.now().isoformat(),
        'seats': {}, 'members': {}, 'messages': [], 'msg_seq': 0
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
    if uid not in room['members']:
        add_message(room, '', '👋 ' + display_name(tg) + ' ገባ', sys=True)
    room['members'][uid] = {'seen': time.time(), 'name': display_name(tg)}
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0
    msgs = [m for m in room['messages'] if m['id'] > since]
    save_data(data)
    return jsonify({
        'success': True,
        'name': room['name'],
        'host': room['creator'],
        'count': len(room['members']),
        'seats': room['seats'],
        'messages': msgs
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


@app.route('/api/gifts/send', methods=['POST'])
def send_gift():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    gift_type = body.get('gift_type')
    recipient_id = body.get('recipient_id')
    data = load_data()
    if uid not in data['users']:
        return jsonify({'success': False, 'error': 'User not found'})
    if recipient_id == uid or recipient_id not in data['users']:
        return jsonify({'success': False, 'error': 'Invalid recipient'})
    gift = data['gifts'].get(gift_type)
    user = data['users'][uid]
    if not gift or user['coins'] < gift['price']:
        return jsonify({'success': False, 'error': 'Not enough coins'})
    user['coins'] -= gift['price']
    data['users'][recipient_id]['coins'] += int(gift['price'] * 0.7)
    data['transactions'].append({
        'from': uid, 'to': recipient_id, 'gift': gift_type,
        'amount': gift['price'], 'timestamp': datetime.now().isoformat()
    })
    save_data(data)
    return jsonify({'success': True, 'remaining_coins': user['coins']})


@app.route('/api/balance', methods=['GET'])
def get_balance():
    uid, _ = current_user_id()
    if not uid:
        return unauthorized()
    data = load_data()
    return jsonify({'coins': data['users'].get(uid, {}).get('coins', 0)})


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
            room_join=True,
            room=room_id,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True))
        .to_jwt()
    )
    return jsonify({
        'success': True,
        'token': token,
        'url': os.environ.get('LIVEKIT_URL', ''),
        'is_host': room['creator'] == uid
    })
