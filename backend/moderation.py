# -*- coding: utf-8 -*-
"""Stage 1, round 2: room admins, chat mute and room block, private rooms,
reports and the Moderator role, and closing your own account.

Additive module: it creates its own tables only. Moderators never get access to
coins, payments or phone numbers.
"""
from flask import request, jsonify, g
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from . import roomrules as RR
except ImportError:
    import roomrules as RR

MAX_ROOM_ADMINS = 10
MIN_ROOM_PASS, MAX_ROOM_PASS = 4, 20
REPORTS_PER_DAY = 10
REPORT_REASONS = ('abuse', 'nudity', 'scam', 'spam', 'underage', 'other')
TELEGRAM_REUSE_AFTER = 30 * 86400      # a closed account's Telegram can verify again after this
# these room routes only remove the caller, or have their own entry check
OPEN_ENDPOINTS = {'room_join', 'room_exit', 'leave_seat', 'guest_leave', 'invite_respond'}


def init_moderation(app, db, d):
    User, Room, Seat, LiveGuest, Presence, Message = d['User'], d['Room'], d['Seat'], d['LiveGuest'], d['Presence'], d['Message']
    Post, PostImage, PostLike, Job, JobImage, Photo = d['Post'], d['PostImage'], d['PostLike'], d['Job'], d['JobImage'], d['Photo']
    Purchase, Withdraw, P2P, PKBattle, Seller = d['Purchase'], d['Withdraw'], d['P2P'], d['PKBattle'], d['Seller']
    SellerOrder = d['SellerOrder']
    now, err, body, num, auth, admin_only, pub = d['now'], d['err'], d['body'], d['num'], d['auth'], d['admin_only'], d['pub']
    parse_token, is_admin, bump, delete_room = d['parse_token'], d['is_admin'], d['bump'], d['delete_room']
    login, notify_admins = d['login'], d['notify_admins']

    class StaffRole(db.Model):
        __tablename__ = 'mod_staff'
        user_id = db.Column(db.Integer, primary_key=True)
        by_id = db.Column(db.Integer)
        ts = db.Column(db.Integer, default=now)

    class RoomAdmin(db.Model):
        # belongs to the owner, not to one room, so it survives a new live
        __tablename__ = 'mod_room_admin'
        host_id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, primary_key=True, index=True)
        ts = db.Column(db.Integer, default=now)

    class Restriction(db.Model):
        __tablename__ = 'mod_restriction'
        host_id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, primary_key=True, index=True)
        mute_until = db.Column(db.Integer, default=0, nullable=False)
        mute_level = db.Column(db.Integer, default=0, nullable=False)
        ban_until = db.Column(db.Integer, default=0, nullable=False)
        ban_level = db.Column(db.Integer, default=0, nullable=False)
        ts = db.Column(db.Integer, default=now)

    class RoomLock(db.Model):
        __tablename__ = 'mod_room_lock'
        room_id = db.Column(db.String(16), primary_key=True)
        pw_hash = db.Column(db.String(256), nullable=False)

    class RoomPass(db.Model):
        __tablename__ = 'mod_room_pass'
        room_id = db.Column(db.String(16), primary_key=True)
        user_id = db.Column(db.Integer, primary_key=True)

    class Report(db.Model):
        __tablename__ = 'mod_report'
        id = db.Column(db.Integer, primary_key=True)
        reporter_id = db.Column(db.Integer, index=True)
        target_id = db.Column(db.Integer, index=True)
        room_id = db.Column(db.String(16), default='')
        reason = db.Column(db.String(10))
        note = db.Column(db.String(200), default='')
        status = db.Column(db.String(8), default='open', index=True)
        handled_by = db.Column(db.Integer)
        ts = db.Column(db.Integer, default=now, index=True)

    class ModLog(db.Model):
        __tablename__ = 'mod_log'
        id = db.Column(db.Integer, primary_key=True)
        staff_id = db.Column(db.Integer, index=True)
        action = db.Column(db.String(20))
        target = db.Column(db.String(24), default='')
        note = db.Column(db.String(200), default='')
        ts = db.Column(db.Integer, default=now, index=True)

    class ClosedAccount(db.Model):
        __tablename__ = 'mod_closed_account'
        user_id = db.Column(db.Integer, primary_key=True)
        phone_key = db.Column(db.String(64), default='', index=True)    # a hash, never the number itself
        ts = db.Column(db.Integer, default=now)

    with app.app_context():
        try: db.create_all()
        except Exception as e: print('moderation create_all:', e)

    # ---- who is who ----
    def is_mod(uid):
        return db.session.get(StaffRole, uid) is not None

    def level_in(host_id, u):
        room_admin = db.session.get(RoomAdmin, (host_id, u.id)) is not None
        return RR.level(is_admin(u), is_mod(u.id), u.id == host_id, room_admin)

    def is_staff(u):
        return is_admin(u) or is_mod(u.id)

    def staff_only(f):
        from functools import wraps

        @wraps(f)
        @auth
        def w(*a, **k):
            if not is_staff(g.user): return err('forbidden', 403)
            return f(*a, **k)
        return w

    def log(action, target='', note=''):
        db.session.add(ModLog(staff_id=g.user.id, action=action, target=str(target)[:24], note=str(note)[:200]))

    def restriction(host_id, uid):
        return db.session.get(Restriction, (host_id, uid))

    def is_banned(host_id, uid):
        x = restriction(host_id, uid)
        return bool(x and x.ban_until > now())

    def muted_until(host_id, uid):
        x = restriction(host_id, uid)
        return x.mute_until if x and x.mute_until > now() else 0

    # ---- entry gate: one check in front of every /api/rooms/<rid>/... route ----
    def entry(r, u):
        lvl = level_in(r.host_id, u)
        problem = RR.entry_problem(lvl, is_banned(r.host_id, u.id),
                                   db.session.get(RoomLock, r.id) is not None,
                                   db.session.get(RoomPass, (r.id, u.id)) is not None)
        if problem == 'banned': return err('You were removed from this room', 403)
        if problem == 'locked': return err('ROOM_LOCKED', 423)
        return None

    def allowed_in(r, u):
        return entry(r, u) is None

    @app.before_request
    def room_gate():
        rid = (request.view_args or {}).get('rid')
        if not rid or not request.path.startswith('/api/rooms/') or request.endpoint in OPEN_ENDPOINTS: return None
        header = request.headers.get('Authorization', '')
        uid = parse_token(header[7:]) if header.startswith('Bearer ') else None
        u = db.session.get(User, uid) if uid else None
        r = db.session.get(Room, rid)
        if not u or u.banned or not r: return None     # the route itself answers these
        return entry(r, u)

    def grant(rid, uid):
        if db.session.get(RoomPass, (rid, uid)): return
        try:
            db.session.add(RoomPass(room_id=rid, user_id=uid)); db.session.commit()
        except IntegrityError:
            db.session.rollback()

    def join_check(r, u, password):
        """Called by the join route. Returns an error reply, or None to let the person in."""
        rid, uid = r.id, u.id
        lock = db.session.get(RoomLock, rid)
        blocked = entry(r, u)
        if blocked is None: return None
        if blocked[1] != 423 or not password or not lock: return blocked
        stored = lock.pw_hash
        key = login['hkey']('rm', f'{uid}:{rid}')
        if not login['hit'](key, 6, 900): return err('Too many wrong passwords. Try again in 15 minutes', 429)
        if not check_password_hash(stored, str(password)[:MAX_ROOM_PASS]): return err('WRONG_ROOM_PASSWORD', 423)
        grant(rid, uid)
        return None

    def password_problem(pw):
        if not MIN_ROOM_PASS <= len(pw) <= MAX_ROOM_PASS:
            return f'Room password: {MIN_ROOM_PASS}-{MAX_ROOM_PASS} characters'
        return None

    def lock_room(rid, pw):
        """Set or remove the password (pw = '' removes it). The caller commits."""
        RoomPass.query.filter(RoomPass.room_id == rid).delete(synchronize_session=False)
        lock = db.session.get(RoomLock, rid)
        if not pw:
            if lock: db.session.delete(lock)
            return
        if lock: lock.pw_hash = generate_password_hash(pw)
        else: db.session.add(RoomLock(room_id=rid, pw_hash=generate_password_hash(pw)))
        # people already watching are not thrown out
        for (uid,) in db.session.query(Presence.user_id).filter(Presence.room_id == rid, Presence.seen > now() - 30):
            db.session.add(RoomPass(room_id=rid, user_id=uid))

    def locked_ids(ids):
        return {x for (x,) in db.session.query(RoomLock.room_id).filter(RoomLock.room_id.in_(list(ids) or ['-']))}

    def drop_room(rid):
        for M in (RoomLock, RoomPass):
            M.query.filter(M.room_id == rid).delete(synchronize_session=False)

    def view(r, u):
        """What the room screen needs to know about the caller."""
        lvl = level_in(r.host_id, u)
        return dict(level=lvl, muted=muted_until(r.host_id, u.id), private=db.session.get(RoomLock, r.id) is not None)

    @app.post('/api/rooms/<rid>/privacy')
    @auth
    def room_privacy(rid):
        r = db.session.get(Room, rid)
        if not r: return err('ROOM_GONE', 404)
        pw = str(body().get('password') or '').strip()
        staff = is_staff(g.user)
        if r.host_id != g.user.id and not (staff and not pw): return err('Only the room owner can do this', 403)
        if pw and password_problem(pw): return err(password_problem(pw))
        lock_room(rid, pw)
        if staff and r.host_id != g.user.id: log('unlock_room', rid)
        db.session.commit(); bump(rid)
        return jsonify(private=bool(pw))

    # ---- room moderation ----
    def remove_from_rooms(host_id, uid):
        """Take a person out of every room of this owner. The caller commits."""
        rids = [x for (x,) in db.session.query(Room.id).filter(Room.host_id == host_id)]
        if not rids: return []
        for M in (Seat, Presence, LiveGuest, RoomPass):
            M.query.filter(M.room_id.in_(rids), M.user_id == uid).delete(synchronize_session=False)
        return rids

    def status_of(host_id, actor, target):
        mine, theirs = level_in(host_id, actor), level_in(host_id, target)
        x = restriction(host_id, target.id); t = now()
        muted = x.mute_until if x and x.mute_until > t else 0
        banned = x.ban_until if x and x.ban_until > t else 0

        def can(action, set_by=0, minutes=10):
            return target.id != actor.id and RR.action_problem(
                action, mine, theirs, target.id == host_id, set_by, minutes, actor.id == host_id) is None

        mute_by = x.mute_level if muted else 0
        ban_by = x.ban_level if banned else 0
        room_admin = db.session.get(RoomAdmin, (host_id, target.id)) is not None
        return dict(muted=muted, banned=banned, room_admin=room_admin, can=dict(
            mute=can('mute', mute_by), unmute=bool(muted) and can('unmute', mute_by),
            mute_forever=can('mute', mute_by, 0), kick=can('kick', ban_by), ban=can('ban', ban_by),
            admin=not room_admin and can('admin'), unadmin=room_admin and can('unadmin')))

    @app.get('/api/rooms/<rid>/mod/<int:uid>')
    @auth
    def room_mod_status(rid, uid):
        r = db.session.get(Room, rid); target = db.session.get(User, uid)
        if not r or not target: return err('not found', 404)
        if level_in(r.host_id, g.user) < RR.ROOM_ADMIN: return err('You are not an admin of this room', 403)
        return jsonify(status_of(r.host_id, g.user, target))

    def apply_action(host_id, actor, target, action, minutes):
        """Check and perform one room action. Returns (error reply or None)."""
        if target.id == actor.id: return err('You cannot do this to yourself')
        mine, theirs = level_in(host_id, actor), level_in(host_id, target)
        x = restriction(host_id, target.id); t = now()
        set_by = 0
        if x and action in ('mute', 'unmute') and x.mute_until > t: set_by = x.mute_level
        if x and action in ('kick', 'ban', 'unban') and x.ban_until > t: set_by = x.ban_level
        problem = RR.action_problem(action, mine, theirs, target.id == host_id, set_by, minutes, actor.id == host_id)
        if problem: return err(problem, 403)
        if action == 'admin':
            if not db.session.get(RoomAdmin, (host_id, target.id)):
                if RoomAdmin.query.filter_by(host_id=host_id).count() >= MAX_ROOM_ADMINS:
                    return err(f'At most {MAX_ROOM_ADMINS} room admins')
                db.session.add(RoomAdmin(host_id=host_id, user_id=target.id))
        elif action == 'unadmin':
            RoomAdmin.query.filter_by(host_id=host_id, user_id=target.id).delete(synchronize_session=False)
        else:
            if not x:
                x = Restriction(host_id=host_id, user_id=target.id, mute_until=0, mute_level=0, ban_until=0, ban_level=0)
                db.session.add(x)
            x.ts = t
            if action == 'mute': x.mute_until, x.mute_level = RR.until(t, minutes), mine
            elif action == 'unmute': x.mute_until, x.mute_level = 0, 0
            elif action == 'unban': x.ban_until, x.ban_level = 0, 0
            else:
                end = RR.FOREVER if action == 'ban' else t + RR.KICK_SECONDS
                if end >= (x.ban_until if x.ban_until > t else 0):     # a short kick leaves a longer block as it is
                    x.ban_until, x.ban_level = end, mine
                if action == 'ban':
                    RoomAdmin.query.filter_by(host_id=host_id, user_id=target.id).delete(synchronize_session=False)
        changed = remove_from_rooms(host_id, target.id) if action in ('kick', 'ban') else []
        if mine >= RR.MOD and actor.id != host_id: log('room_' + action, target.id, f'host {host_id}')
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); return err('Try again', 409)
        for rid in changed: bump(rid)
        return None

    @app.post('/api/rooms/<rid>/mod')
    @auth
    def room_mod_action(rid):
        r = db.session.get(Room, rid)
        if not r: return err('ROOM_GONE', 404)
        b = body(); target = db.session.get(User, num(b.get('uid')))
        if not target: return err('user not found', 404)
        host_id = r.host_id
        problem = apply_action(host_id, g.user, target, str(b.get('action')), num(b.get('minutes'), 10))
        return problem or jsonify(ok=1)

    def may_touch_seat(r, actor, target_id, action):
        """For the older mute / remove buttons on seats and live guests."""
        target = db.session.get(User, target_id)
        theirs = level_in(r.host_id, target) if target else RR.USER
        return RR.seat_problem(action, level_in(r.host_id, actor), theirs, target_id == r.host_id) is None

    # the owner manages their own lists even when no room is open
    @app.get('/api/host/moderation')
    @auth
    def host_moderation():
        hid = g.user.id; t = now()
        admins = [x for (x,) in db.session.query(RoomAdmin.user_id).filter(RoomAdmin.host_id == hid)]
        rows = Restriction.query.filter(Restriction.host_id == hid, or_(Restriction.mute_until > t, Restriction.ban_until > t)) \
            .order_by(Restriction.ts.desc()).limit(100).all()
        ids = admins + [x.user_id for x in rows]
        users = {u.id: u for u in User.query.filter(User.id.in_(ids or [0])).all()}
        return jsonify(
            admins=[pub(users[i]) for i in admins if i in users],
            muted=[dict(pub(users[x.user_id]), until=x.mute_until) for x in rows if x.mute_until > t and x.user_id in users],
            blocked=[dict(pub(users[x.user_id]), until=x.ban_until) for x in rows if x.ban_until > t and x.user_id in users])

    @app.post('/api/host/moderation')
    @auth
    def host_moderation_remove():
        b = body(); action = str(b.get('action')); target = db.session.get(User, num(b.get('uid')))
        if action not in ('unmute', 'unban', 'unadmin') or not target: return err('bad request')
        problem = apply_action(g.user.id, g.user, target, action, 10)
        return problem or jsonify(ok=1)

    # ---- reports ----
    @app.post('/api/report')
    @auth
    def report_create():
        b = body(); uid = g.user.id; target = db.session.get(User, num(b.get('uid')))
        if not target or target.id == uid: return err('user not found', 404)
        reason = b.get('reason') if b.get('reason') in REPORT_REASONS else 'other'
        note = str(b.get('note') or '').strip()[:200]
        rid = str(b.get('room_id') or '')[:16]
        target_id = target.id
        if Report.query.filter_by(reporter_id=uid, target_id=target_id, status='open').first():
            return jsonify(ok=1)
        if not login['hit'](login['hkey']('rpt', str(uid)), REPORTS_PER_DAY, 86400):
            return err('You sent too many reports today', 429)
        db.session.add(Report(reporter_id=uid, target_id=target_id, room_id=rid, reason=reason, note=note))
        db.session.commit()
        return jsonify(ok=1)

    # ---- moderator panel: no coins, payments or phone numbers here ----
    def card(u):
        return dict(pub(u), suspended=bool(u.banned), staff=is_staff(u)) if u else None

    @app.get('/api/mod/reports')
    @staff_only
    def mod_reports():
        rows = Report.query.filter_by(status='open').order_by(Report.id.desc()).limit(50).all()
        ids = {x for r in rows for x in (r.reporter_id, r.target_id)}
        users = {u.id: u for u in User.query.filter(User.id.in_(list(ids) or [0])).all()}
        rooms = {x.id: x for x in Room.query.filter(Room.id.in_([r.room_id for r in rows if r.room_id] or ['-'])).all()}
        return jsonify(items=[dict(id=r.id, reason=r.reason, note=r.note, ts=r.ts,
                                   reporter=card(users.get(r.reporter_id)), target=card(users.get(r.target_id)),
                                   room=dict(id=r.room_id, name=rooms[r.room_id].name) if r.room_id in rooms else None)
                              for r in rows])

    @app.post('/api/mod/report')
    @staff_only
    def mod_report_close():
        r = db.session.get(Report, num(body().get('id')))
        if not r or r.status != 'open': return err('Report not found', 404)
        r.status = 'closed'; r.handled_by = g.user.id; log('close_report', r.id)
        db.session.commit(); return jsonify(ok=1)

    @app.get('/api/mod/users')
    @staff_only
    def mod_users():
        q = (request.args.get('q') or '').strip()[:30]
        if len(q) < 2: return jsonify(items=[])
        us = User.query.filter(User.username.ilike(f'%{q}%')).order_by(User.id.desc()).limit(20).all()
        return jsonify(items=[card(u) for u in us])

    def close_rooms_of(uid):
        for (rid,) in db.session.query(Room.id).filter(Room.host_id == uid).all(): delete_room(rid)
        left = {x for M in (Seat, LiveGuest) for (x,) in db.session.query(M.room_id).filter(M.user_id == uid)}
        for M in (Seat, Presence, LiveGuest):
            M.query.filter(M.user_id == uid).delete(synchronize_session=False)
        db.session.commit()
        for rid in left: bump(rid)

    @app.post('/api/mod/suspend')
    @staff_only
    def mod_suspend():
        """A moderator may suspend a normal user. Only an admin can lift it (admin users tab)."""
        b = body(); u = db.session.get(User, num(b.get('id')))
        reason = str(b.get('reason') or '').strip()[:200]
        if not u: return err('user not found', 404)
        if is_staff(u): return err('Cannot suspend platform staff', 403)
        if len(reason) < 3: return err('Write the reason')
        if u.banned: return err('Already suspended')
        uid, name = u.id, u.username
        u.banned = True; log('suspend', uid, reason)
        Report.query.filter_by(target_id=uid, status='open').update({'status': 'closed', 'handled_by': g.user.id},
                                                                    synchronize_session=False)
        db.session.commit(); close_rooms_of(uid)
        notify_admins(f'🛡 Moderator suspended: {name} — {reason}')
        return jsonify(ok=1)

    @app.post('/api/mod/close-room')
    @staff_only
    def mod_close_room():
        r = db.session.get(Room, str(body().get('id') or '')[:16])
        if not r: return err('ROOM_GONE', 404)
        owner = db.session.get(User, r.host_id)
        if owner and is_admin(owner) and not is_admin(g.user): return err('Cannot close an admin room', 403)
        rid = r.id; log('close_room', rid, r.name)
        db.session.commit(); delete_room(rid); return jsonify(ok=1)

    @app.post('/api/admin/moderator')
    @admin_only
    def admin_moderator():
        b = body(); u = db.session.get(User, num(b.get('id')))
        if not u: return err('user not found', 404)
        row = db.session.get(StaffRole, u.id)
        if b.get('on') and not row:
            if u.banned: return err('User is suspended')
            db.session.add(StaffRole(user_id=u.id, by_id=g.user.id)); log('add_moderator', u.id)
        elif not b.get('on') and row:
            db.session.delete(row); log('remove_moderator', u.id)
        db.session.commit(); return jsonify(ok=1)

    @app.get('/api/admin/mod-log')
    @admin_only
    def admin_mod_log():
        rows = ModLog.query.order_by(ModLog.id.desc()).limit(80).all()
        ids = {r.staff_id for r in rows} | {num(r.target, 0) for r in rows}
        names = {u.id: u.username for u in User.query.filter(User.id.in_(list(ids) or [0])).all()}
        return jsonify(items=[dict(staff=names.get(r.staff_id, '?'), action=r.action, note=r.note, ts=r.ts,
                                   target=names.get(num(r.target, 0), r.target)) for r in rows])

    def mod_ids(ids):
        return {x for (x,) in db.session.query(StaffRole.user_id).filter(StaffRole.user_id.in_(list(ids) or [0]))}

    # ---- closing your own account ----
    def blockers(u):
        out = []
        if is_admin(u): out.append('An admin account cannot be closed here.')
        if Withdraw.query.filter_by(user_id=u.id, status='pending').first(): out.append('You have a withdrawal waiting.')
        if Purchase.query.filter_by(user_id=u.id, status='pending').first(): out.append('You have a coin purchase waiting.')
        if db.session.query(Seller.user_id).filter(Seller.user_id == u.id, Seller.status != 'rejected').first() \
                or SellerOrder.query.filter_by(seller_id=u.id, status='pending').first():
            out.append('You have a seller shop. Contact support to close it first.')
        if P2P.query.filter_by(creator_id=u.id, status='open').first(): out.append('Cancel your open P2P battle first.')
        if PKBattle.query.filter(or_(PKBattle.host1_id == u.id, PKBattle.host2_id == u.id), PKBattle.status == 'active').first():
            out.append('Your PK match is still running.')
        return out

    @app.get('/api/account/close-check')
    @auth
    def close_check():
        u = g.user
        return jsonify(blockers=blockers(u), coins=u.coins, income=u.earnings + (u.commission or 0),
                       has_password=login['has_password'](u.id), reuse_days=TELEGRAM_REUSE_AFTER // 86400)

    @app.post('/api/account/close')
    @auth
    def close_account():
        b = body(); uid = g.user.id
        if str(b.get('confirm') or '').strip().upper() != 'DELETE': return err('Type DELETE to confirm')
        if login['has_password'](uid):
            if not login['hit'](login['hkey']('del', str(uid)), 6, 900): return err('Too many attempts. Try again in 15 minutes', 429)
            if not login['check_password'](uid, b.get('password')): return err('Password is wrong')
        u = db.session.query(User).filter(User.id == uid).populate_existing().with_for_update().first()
        problems = blockers(u)
        if problems:
            db.session.rollback(); return err(problems[0], 409)
        name = u.username
        # The row stays: purchases, gifts and the invite chain must keep adding up.
        # Balances are frozen on the closed account, not erased.
        u.banned = True; u.username = 'Deleted account'; u.bio = ''; u.phone = ''; u.tg_photo = ''; u.has_photo = False
        u.host_status = 'none'; u.is_agent = False; u.frame = ''
        closed = ClosedAccount(user_id=uid); db.session.add(closed)
        posts = [x for (x,) in db.session.query(Post.id).filter(Post.user_id == uid)]
        if posts:
            for M in (PostLike, PostImage):
                M.query.filter(M.post_id.in_(posts)).delete(synchronize_session=False)
            Post.query.filter(Post.id.in_(posts)).delete(synchronize_session=False)
        jobs = [x for (x,) in db.session.query(Job.id).filter(Job.user_id == uid)]
        if jobs:
            JobImage.query.filter(JobImage.job_id.in_(jobs)).delete(synchronize_session=False)
            Job.query.filter(Job.id.in_(jobs)).delete(synchronize_session=False)
        for M in (Photo, Message):
            M.query.filter(M.user_id == uid).delete(synchronize_session=False)
        delete_user(uid)
        d['cleanup'](uid)              # follows and notifications
        closed.phone_key = login['close_login'](uid)      # frees the phone number
        db.session.commit()
        close_rooms_of(uid)
        notify_admins(f'🗑 Account closed by its owner: {name} (id {uid})')
        return jsonify(ok=1)

    def telegram_released(uid):
        """True when this Telegram link belongs to an account closed long enough ago."""
        x = db.session.get(ClosedAccount, uid)
        return bool(x and x.ts < now() - TELEGRAM_REUSE_AFTER)

    def is_closed(uid):
        return db.session.get(ClosedAccount, uid) is not None

    def recently_closed(phone_key):
        return ClosedAccount.query.filter(ClosedAccount.phone_key == phone_key,
                                          ClosedAccount.ts >= now() - TELEGRAM_REUSE_AFTER).first() is not None

    def delete_user(uid):       # runs inside the caller's transaction
        StaffRole.query.filter(StaffRole.user_id == uid).delete(synchronize_session=False)
        RoomAdmin.query.filter(or_(RoomAdmin.host_id == uid, RoomAdmin.user_id == uid)).delete(synchronize_session=False)
        Restriction.query.filter(Restriction.host_id == uid).delete(synchronize_session=False)
        RoomPass.query.filter(RoomPass.user_id == uid).delete(synchronize_session=False)

    return dict(is_mod=is_mod, is_staff=is_staff, mod_ids=mod_ids, allowed_in=allowed_in, entry=entry, join_check=join_check,
                grant=grant, is_banned=is_banned, muted_until=muted_until, view=view, lock_room=lock_room,
                password_problem=password_problem, locked_ids=locked_ids, drop_room=drop_room,
                may_touch_seat=may_touch_seat, delete_user=delete_user, telegram_released=telegram_released, is_closed=is_closed, recently_closed=recently_closed, log=log)
