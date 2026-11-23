from flask import Flask, render_template, request, jsonify
import json, os, hmac, hashlib, time
from urllib.parse import parse_qsl
from datetime import datetime
from livekit import api as lkapi

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'db.json')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')


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
            'username': tg.get('first_name', 'User'),
            'coins': 100,
            'joined': datetime.now().isoformat()
        }
        save_data(data)
    return jsonify({'success': True, 'user_id': uid, 'user': data['users'][uid]})


@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    return jsonify(load_data()['rooms'])


@app.route('/api/rooms/create', methods=['POST'])
def create_room():
    uid, tg = current_user_id()
    if not uid:
        return unauthorized()
    body = request.get_json(silent=True) or {}
    room_id = str(body.get('room_id', ''))[:40]
    room_name = str(body.get('room_name', '')).strip()[:50]
    if not room_id or not room_name:
        return jsonify({'success': False, 'error': 'Invalid room'})
    data = load_data()
    data['rooms'][room_id] = {
        'name': room_name, 'creator': uid,
        'creator_name': tg.get('first_name', 'User'),
        'created': datetime.now().isoformat(), 'listeners': []
    }
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
    is_host = room['creator'] == uid
    token = (
        lkapi.AccessToken(
            os.environ.get('LIVEKIT_API_KEY', ''),
            os.environ.get('LIVEKIT_API_SECRET', ''))
        .with_identity(uid)
        .with_name(tg.get('first_name', 'User'))
        .with_grants(lkapi.VideoGrants(
            room_join=True,
            room=room_id,
            can_publish=is_host,
            can_subscribe=True,
            can_publish_data=True))
        .to_jwt()
    )
    return jsonify({
        'success': True,
        'token': token,
        'url': os.environ.get('LIVEKIT_URL', ''),
        'is_host': is_host
    })
