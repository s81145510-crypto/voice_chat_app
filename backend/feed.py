# -*- coding: utf-8 -*-
"""Phase 2: follow, public profiles, home feed and rankings."""
from datetime import datetime
from flask import request, jsonify, g
from sqlalchemy import func, select, or_, and_
from sqlalchemy.exc import IntegrityError

MAX_FOLLOWING = 2000


def init_feed(app, db, d):
    User, Room, Seat, Presence, Tx, now = d['User'], d['Room'], d['Seat'], d['Presence'], d['Tx'], d['now']
    err, body, num, auth, pub = d['err'], d['body'], d['num'], d['auth'], d['pub']
    limited, gc_rooms = d['limited'], d['gc_rooms']

    class Follow(db.Model):
        __tablename__ = 'user_follow'
        follower_id = db.Column(db.Integer, primary_key=True)
        target_id = db.Column(db.Integer, primary_key=True, index=True)
        ts = db.Column(db.Integer, default=now)

    class AppNotification(db.Model):
        __tablename__ = 'app_notification'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, index=True, nullable=False)
        actor_id = db.Column(db.Integer, nullable=True)
        kind = db.Column(db.String(32), nullable=False)
        title = db.Column(db.String(100), nullable=False)
        body = db.Column(db.String(240), default='')
        target = db.Column(db.String(120), default='')
        read_at = db.Column(db.Integer, nullable=True)
        ts = db.Column(db.Integer, default=now, index=True)

    class UserBlock(db.Model):
        __tablename__ = 'user_block'
        blocker_id = db.Column(db.Integer, primary_key=True)
        blocked_id = db.Column(db.Integer, primary_key=True, index=True)
        ts = db.Column(db.Integer, default=now)

    class UserReport(db.Model):
        __tablename__ = 'user_report'
        id = db.Column(db.Integer, primary_key=True)
        reporter_id = db.Column(db.Integer, nullable=False, index=True)
        target_type = db.Column(db.String(12), nullable=False)
        target_id = db.Column(db.Integer, nullable=False, index=True)
        reason = db.Column(db.String(32), nullable=False)
        details = db.Column(db.String(500), default='')
        status = db.Column(db.String(16), default='pending', nullable=False, index=True)
        ts = db.Column(db.Integer, default=now, index=True)

    def notify_user(user_id, actor_id, kind, title, message='', target=''):
        if not db.session.get(User, user_id): return False
        db.session.add(AppNotification(user_id=user_id, actor_id=actor_id, kind=str(kind)[:32], title=str(title)[:100], body=str(message)[:240], target=str(target)[:120]))
        db.session.commit()
        return True

    with app.app_context():
        try: db.create_all()
        except Exception as e: print('feed create_all:', e)

    def active_rooms():
        t = now()
        return Room.query.filter(or_(
            and_(Room.kind == 'live', Room.last_active > t - 90),
            and_(or_(Room.kind == 'voice', Room.kind.is_(None)), Room.last_active > t - 6 * 3600)))

    def counts(uid):
        followers = db.session.query(func.count()).select_from(Follow).join(User, User.id == Follow.follower_id).filter(Follow.target_id == uid, User.banned == False).scalar() or 0
        following = db.session.query(func.count()).select_from(Follow).join(User, User.id == Follow.target_id).filter(Follow.follower_id == uid, User.banned == False).scalar() or 0
        return followers, following

    def my_following(ids):
        return {x for (x,) in db.session.query(Follow.target_id).filter(Follow.follower_id == g.user.id, Follow.target_id.in_(ids or [0]))}

    def visible_user(uid):
        u = db.session.get(User, uid)
        return u if u and not u.banned else None

    @app.get('/api/feed')
    @auth
    def feed_list():
        gc_rooms(); t = now(); tab = request.args.get('tab', 'popular')
        q = active_rooms()
        if tab == 'live': q = q.filter(Room.kind == 'live')
        elif tab == 'voice': q = q.filter(or_(Room.kind == 'voice', Room.kind.is_(None)))
        elif tab == 'follow':
            q = q.filter(Room.host_id.in_(select(Follow.target_id).where(Follow.follower_id == g.user.id)))
        blocked = {x for (x,) in db.session.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == g.user.id).all()}
        blockers = {x for (x,) in db.session.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == g.user.id).all()}
        hidden_hosts = blocked | blockers
        if hidden_hosts: q = q.filter(~Room.host_id.in_(hidden_hosts))
        rs = q.order_by(Room.created.desc() if tab == 'new' else Room.last_active.desc()).limit(100).all()
        ids = [r.id for r in rs] or ['-']
        online = dict(db.session.query(Presence.room_id, func.count()).filter(Presence.room_id.in_(ids), Presence.seen > t - 20).group_by(Presence.room_id).all())
        seated = dict(db.session.query(Seat.room_id, func.count()).filter(Seat.room_id.in_(ids)).group_by(Seat.room_id).all())
        host_ids = list({r.host_id for r in rs})
        hosts = {u.id: u for u in User.query.filter(User.id.in_(host_ids or [0]), User.banned == False).all()}
        followed = my_following(host_ids)
        out = [dict(id=r.id, kind=r.kind or 'voice', name=r.name, topic=r.topic, online=online.get(r.id, 0), seated=seated.get(r.id, 0),
                    host=pub(hosts[r.host_id]), following=r.host_id in followed) for r in rs if r.host_id in hosts]
        if tab != 'new':   # video lives first, then the busiest rooms
            out.sort(key=lambda x: (x['kind'] != 'live', -x['online']))
        return jsonify(rooms=out)

    @app.get('/api/users/<int:uid>')
    @auth
    def user_profile(uid):
        u = visible_user(uid)
        if not u: return err('User not found', 404)
        if db.session.get(UserBlock, (g.user.id, uid)) or db.session.get(UserBlock, (uid, g.user.id)):
            return err('User not found', 404)
        followers, following = counts(uid)
        r = active_rooms().filter(Room.host_id == uid).order_by((Room.kind == 'live').desc(), Room.last_active.desc()).first()
        # only public fields: never phone, login ID or balance
        return jsonify(user=pub(u), followers=followers, following=following, sent=u.gifts_sent, recv=u.gifts_recv,
                       is_following=bool(db.session.get(Follow, (g.user.id, uid))), me=uid == g.user.id,
                       is_blocked=bool(db.session.get(UserBlock, (g.user.id, uid))),
                       room=dict(id=r.id, kind=r.kind or 'voice', name=r.name) if r else None)

    @app.get('/api/notifications')
    @auth
    def notifications_list():
        rows = AppNotification.query.filter_by(user_id=g.user.id).order_by(AppNotification.id.desc()).limit(30).all()
        unread = AppNotification.query.filter_by(user_id=g.user.id, read_at=None).count()
        return jsonify(unread=unread, items=[dict(id=x.id, actor_id=x.actor_id, kind=x.kind, title=x.title, body=x.body, target=x.target, ts=x.ts, read=bool(x.read_at)) for x in rows])

    @app.post('/api/notifications/read')
    @auth
    def notifications_read():
        b = body(); q = AppNotification.query.filter_by(user_id=g.user.id, read_at=None)
        nid = num(b.get('id'))
        if nid > 0: q = q.filter_by(id=nid)
        count = q.update({AppNotification.read_at: now()}, synchronize_session=False)
        db.session.commit()
        return jsonify(ok=1, updated=count)

    @app.post('/api/follow/<int:uid>')
    @auth
    def follow_set(uid):
        if uid == g.user.id: return err('You cannot follow yourself')
        if not visible_user(uid): return err('User not found', 404)
        if db.session.get(UserBlock, (g.user.id, uid)) or db.session.get(UserBlock, (uid, g.user.id)):
            return err('User not found', 404)
        if limited('follow', 0.5): return err('Slow down', 429)
        want = bool(body().get('follow'))
        row = db.session.get(Follow, (g.user.id, uid))
        created = False
        try:
            if want and not row:
                if Follow.query.filter_by(follower_id=g.user.id).count() >= MAX_FOLLOWING: return err('You follow too many people')
                db.session.add(Follow(follower_id=g.user.id, target_id=uid)); created = True
            elif not want and row:
                db.session.delete(row)
            db.session.commit()
        except IntegrityError:
            db.session.rollback(); created = False
        if created:
            notify_user(uid, g.user.id, 'follow', 'New follower', f'{g.user.username} started following you', f'profile:{g.user.id}')
        return jsonify(following=want, followers=counts(uid)[0])

    @app.get('/api/users/<int:uid>/follows')
    @auth
    def follow_list(uid):
        if not visible_user(uid): return err('User not found', 404)
        followers = request.args.get('type') != 'following'
        other = Follow.follower_id if followers else Follow.target_id
        mine = Follow.target_id if followers else Follow.follower_id
        us = db.session.query(User).join(Follow, other == User.id).filter(mine == uid, User.banned == False).order_by(Follow.ts.desc()).limit(100).all()
        return jsonify(users=[pub(u) for u in us])

    @app.post('/api/users/<int:uid>/block')
    @auth
    def user_block(uid):
        if uid == g.user.id: return err('You cannot block yourself')
        target = visible_user(uid)
        if not target: return err('User not found', 404)
        if limited('block', 0.5): return err('Slow down', 429)
        want = bool((request.get_json(silent=True) or {}).get('blocked'))
        row = db.session.get(UserBlock, (g.user.id, uid))
        try:
            if want and not row:
                db.session.add(UserBlock(blocker_id=g.user.id, blocked_id=uid))
                Follow.query.filter(or_(
                    and_(Follow.follower_id == g.user.id, Follow.target_id == uid),
                    and_(Follow.follower_id == uid, Follow.target_id == g.user.id)
                )).delete(synchronize_session=False)
            elif not want and row:
                db.session.delete(row)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        return jsonify(blocked=bool(db.session.get(UserBlock, (g.user.id, uid))))

    @app.get('/api/blocks')
    @auth
    def user_blocks():
        rows = db.session.query(UserBlock, User).join(User, User.id == UserBlock.blocked_id).filter(
            UserBlock.blocker_id == g.user.id, User.banned == False
        ).order_by(UserBlock.ts.desc()).limit(200).all()
        return jsonify(users=[dict(pub(u), blocked_at=b.ts) for b, u in rows])

    @app.post('/api/users/<int:uid>/report')
    @auth
    def user_report(uid):
        if uid == g.user.id: return err('You cannot report yourself')
        target = visible_user(uid)
        if not target: return err('User not found', 404)
        if limited('report', 30): return err('Please wait before sending another report', 429)
        b = request.get_json(silent=True) or {}
        reason = str(b.get('reason', '')).strip().lower()
        allowed = {'harassment', 'hate', 'spam', 'impersonation', 'sexual_content', 'scam', 'other'}
        if reason not in allowed: return err('Choose a valid report reason')
        details = str(b.get('details', '')).strip()[:500]
        recent = UserReport.query.filter_by(reporter_id=g.user.id, target_type='user', target_id=uid).filter(
            UserReport.ts > now() - 86400
        ).first()
        if recent: return err('You already reported this user recently', 409)
        db.session.add(UserReport(reporter_id=g.user.id, target_type='user', target_id=uid, reason=reason, details=details))
        db.session.commit()
        return jsonify(ok=1, message='Report submitted for review')

    _cache = {}

    def since(period):
        t = now(); day = t - (t + 3 * 3600) % 86400            # midnight in Addis Ababa
        if period == 'week':
            day -= datetime.utcfromtimestamp(day + 3 * 3600).weekday() * 86400   # Monday
        return day

    def ranked(kind, period):
        if period == 'all':
            col = User.gifts_sent if kind == 'senders' else User.gifts_recv
            return [(u, int(getattr(u, col.key))) for u in User.query.filter(col > 0, User.banned == False).order_by(col.desc()).limit(30).all()]
        if kind == 'senders':
            who, total = Tx.user_id, func.sum(-Tx.amount)
            where = [Tx.kind.in_(['gift', 'lucky', 'pk_gift']), Tx.amount < 0, Tx.ts >= since(period)]
        else:
            gifts = db.metadata.tables.get('fin_gift_tx')       # table created by finance.py
            if gifts is None: return []
            who, total = gifts.c.receiver_id, func.sum(gifts.c.total)
            where = [gifts.c.ts >= since(period)]
        rows = db.session.query(who, total).join(User, User.id == who).filter(User.banned == False, *where).group_by(who).order_by(total.desc()).limit(30).all()
        users = {u.id: u for u in User.query.filter(User.id.in_([i for i, _ in rows] or [0])).all()}
        return [(users[i], int(v or 0)) for i, v in rows if i in users]

    @app.get('/api/ranking')
    @auth
    def ranking():
        kind = 'receivers' if request.args.get('kind') == 'receivers' else 'senders'
        period = request.args.get('period') if request.args.get('period') in ('day', 'week', 'all') else 'day'
        hit = _cache.get((kind, period))
        if not hit or now() - hit[0] > 30:
            hit = _cache[(kind, period)] = (now(), [dict(pub(u), v=v) for u, v in ranked(kind, period)])
        return jsonify(items=hit[1])

    def delete_follows(uid):
        Follow.query.filter(or_(Follow.follower_id == uid, Follow.target_id == uid)).delete(synchronize_session=False)

    def hidden_user_ids(uid):
        blocked = {x for (x,) in db.session.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == uid)}
        blockers = {x for (x,) in db.session.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == uid)}
        return blocked | blockers

    return dict(delete_follows=delete_follows, notify_user=notify_user, hidden_user_ids=hidden_user_ids)
