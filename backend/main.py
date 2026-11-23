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
    
    u['photo'] = f'/api/profile/photo/{uid}'
    save_data(data)
    
    return jsonify({'success': True, 'photo': u['photo']})

@app.route('/api/profile/photo/<uid>', methods=['GET'])
def get_profile_photo(uid):
    path = os.path.join(SHOT_DIR, f'profile_{uid}.jpg')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'profile_{uid}.png')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'profile_{uid}.webp')
    if not os.path.exists(path):
        return jsonify({'error': 'ፎቶ የለም'}), 404
    
    ext = path.rsplit('.', 1)[-1]
    mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    with open(path, 'rb') as f:
        return Response(f.read(), mimetype=mime)

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
    path = os.path.join(SHOT_DIR, f'{pid}.jpg')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'{pid}.png')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'{pid}.webp')
    if not os.path.exists(path):
        return jsonify({'error': 'ፎቶ የለም'}), 404
    
    ext = path.rsplit('.', 1)[-1]
    mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    with open(path, 'rb') as f:
        return Response(f.read(), mimetype=mime)

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
    path = os.path.join(SHOT_DIR, f'{jid}.jpg')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'{jid}.png')
    if not os.path.exists(path):
        path = os.path.join(SHOT_DIR, f'{jid}.webp')
    if not os.path.exists(path):
        return jsonify({'error': 'ፎቶ የለም'}), 404
    
    ext = path.rsplit('.', 1)[-1]
    mime = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    with open(path, 'rb') as f:
        return Response(f.read(), mimetype=mime)
