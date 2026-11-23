from flask import Flask, render_template, request, jsonify
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'db.json')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = 'voice_chat_secret_key_2024'

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {
        'users': {},
        'rooms': {},
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    data = load_data()
    user_id = request.json.get('user_id')
    username = request.json.get('username')
    if user_id not in data['users']:
        data['users'][user_id] = {'username': username, 'coins': 100, 'joined': datetime.now().isoformat()}
        save_data(data)
    return jsonify({'success': True, 'user': data['users'][user_id]})

@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    return jsonify(load_data()['rooms'])

@app.route('/api/rooms/create', methods=['POST'])
def create_room():
    data = load_data()
    room_id = request.json.get('room_id')
    room_name = request.json.get('room_name')
    user_id = request.json.get('user_id')
    data['rooms'][room_id] = {'name': room_name, 'creator': user_id, 'created': datetime.now().isoformat(), 'listeners': []}
    save_data(data)
    return jsonify({'success': True})

@app.route('/api/gifts/send', methods=['POST'])
def send_gift():
    data = load_data()
    user_id = request.json.get('user_id')
    gift_type = request.json.get('gift_type')
    recipient_id = request.json.get('recipient_id')
    if user_id not in data['users']:
        return jsonify({'success': False, 'error': 'User not found'})
    user = data['users'][user_id]
    gift = data['gifts'].get(gift_type)
    if not gift or user['coins'] < gift['price']:
        return jsonify({'success': False, 'error': 'Not enough coins'})
    user['coins'] -= gift['price']
    if recipient_id in data['users']:
        data['users'][recipient_id]['coins'] += int(gift['price'] * 0.7)
    data['transactions'].append({'from': user_id, 'to': recipient_id, 'gift': gift_type, 'amount': gift['price'], 'timestamp': datetime.now().isoformat()})
    save_data(data)
    return jsonify({'success': True, 'remaining_coins': user['coins']})

@app.route('/api/balance', methods=['GET'])
def get_balance():
    data = load_data()
    user_id = request.args.get('user_id')
    return jsonify({'coins': data['users'].get(user_id, {}).get('coins', 0)})
