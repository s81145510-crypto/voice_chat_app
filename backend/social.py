# -*- coding: utf-8 -*-
"""Stage 1: in-app notifications, room viewer list, live likes and PK history.

Additive module: it creates its own two tables and never changes existing ones.
Notifications are written on their own database connection, after the caller has
committed, so a failed notification can never undo or half-save a coin movement.
"""
import json
from flask import request, jsonify, g
from sqlalchemy import select, insert, update, delete, func, or_
from sqlalchemy.exc import IntegrityError

KEEP_PER_USER = 100
KEEP_SECONDS = 30 * 86400
FOLLOW_QUIET = 86400        # follow / unfollow / follow again does not notify twice a day
LIVE_QUIET = 600            # restarting a live does not ring followers again
GIFT_MERGE = 600            # gifts from the same person become one line while unread
MAX_LIVE_FANOUT = 2000
MAX_LIKE_BATCH = 15


def init_social(app, db, d):
    User, Room, Presence, PKBattle, now = d['User'], d['Room'], d['Presence'], d['PKBattle'], d['now']
    err, body, num, auth, pub, limited = d['err'], d['body'], d['num'], d['auth'], d['pub'], d['limited']

    class Notice(db.Model):
        __tablename__ = 'social_notice'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, index=True, nullable=False)
        kind = db.Column(db.String(10), nullable=False)       # follow | live | gift | system
        actor_id = db.Column(db.Integer, index=True)
        ref = db.Column(db.String(16), default='')            # room id for "live"
        data = db.Column(db.Text, default='')
        seen = db.Column(db.Boolean, default=False, nullable=False)
        ts = db.Column(db.Integer, default=now, index=True)

    class RoomLike(db.Model):
        __tablename__ = 'social_room_like'
        room_id = db.Column(db.String(16), primary_key=True)
        count = db.Column(db.Integer, default=0, nullable=False)

    with app.app_context():
        try: db.create_all()
        except Exception as e: print('social create_all:', e)

    N = Notice.__table__

    def row(user_id, kind, actor_id=None, ref='', data=None):
        return dict(user_id=user_id, kind=kind, actor_id=actor_id, ref=ref, seen=False, ts=now(),
                    data=json.dumps(data or {}, ensure_ascii=False))

    # ---- writers: each one uses its own transaction and never raises ----
    def follow(actor_id, target_id):
        try:
            with db.engine.begin() as c:
                recent = c.execute(select(N.c.id).where(
                    N.c.user_id == target_id, N.c.kind == 'follow', N.c.actor_id == actor_id,
                    N.c.ts > now() - FOLLOW_QUIET).limit(1)).first()
                if not recent: c.execute(insert(N).values(**row(target_id, 'follow', actor_id)))
        except Exception as e: print('notice follow:', e)

    def host_live(host_id, room_id, room_name):
        try:
            follows = db.metadata.tables.get('user_follow')      # created by feed.py
            if follows is None: return
            with db.engine.begin() as c:
                fans = [x for (x,) in c.execute(select(follows.c.follower_id).where(follows.c.target_id == host_id)
                                                .order_by(follows.c.ts.desc()).limit(MAX_LIVE_FANOUT))]
                if not fans: return
                mine = [N.c.kind == 'live', N.c.actor_id == host_id]
                quiet = {x for (x,) in c.execute(select(N.c.user_id).where(
                    *mine, N.c.ts > now() - LIVE_QUIET, N.c.user_id.in_(fans)))}
                if quiet:   # point the earlier line at the new room, without ringing again
                    c.execute(update(N).where(*mine, N.c.user_id.in_(list(quiet)))
                              .values(ref=room_id, data=json.dumps({'room': room_name}, ensure_ascii=False)))
                new = [x for x in fans if x not in quiet]
                if new:
                    c.execute(delete(N).where(*mine, N.c.user_id.in_(new)))
                    c.execute(insert(N), [row(x, 'live', host_id, room_id, {'room': room_name}) for x in new])
        except Exception as e: print('notice live:', e)

    def gift(actor_id, target_id, emoji, qty, coins):
        try:
            with db.engine.begin() as c:
                old = c.execute(select(N.c.id, N.c.data).where(
                    N.c.user_id == target_id, N.c.kind == 'gift', N.c.actor_id == actor_id,
                    N.c.seen.is_(False), N.c.ts > now() - GIFT_MERGE).order_by(N.c.id.desc()).limit(1)).first()
                info = dict(emoji=emoji, qty=qty, coins=coins)
                if old:
                    prev = json.loads(old.data or '{}')
                    info['coins'] += int(prev.get('coins', 0))
                    info['qty'] += int(prev.get('qty', 0))
                    c.execute(update(N).where(N.c.id == old.id).values(data=json.dumps(info, ensure_ascii=False), ts=now()))
                else:
                    c.execute(insert(N).values(**row(target_id, 'gift', actor_id, data=info)))
        except Exception as e: print('notice gift:', e)

    def system(tg_id, text):
        """Copy of a Telegram message, so phone-number accounts see it inside the app."""
        if not tg_id: return
        try:
            users = User.__table__
            with db.engine.begin() as c:
                hit = c.execute(select(users.c.id).where(users.c.tg_id == str(tg_id)).limit(1)).first()
                if hit: c.execute(insert(N).values(**row(hit.id, 'system', data={'text': str(text)[:500]})))
        except Exception as e: print('notice system:', e)

    # ---- notification routes ----
    def prune(uid):
        mine = Notice.query.filter(Notice.user_id == uid)
        mine.filter(Notice.ts < now() - KEEP_SECONDS).delete(synchronize_session=False)
        extra = db.session.query(Notice.id).filter(Notice.user_id == uid).order_by(Notice.ts.desc(), Notice.id.desc()).offset(KEEP_PER_USER).first()
        if extra:
            keep = [x for (x,) in db.session.query(Notice.id).filter(Notice.user_id == uid).order_by(Notice.ts.desc(), Notice.id.desc()).limit(KEEP_PER_USER)]
            Notice.query.filter(Notice.user_id == uid, Notice.id.notin_(keep)).delete(synchronize_session=False)
        db.session.commit()

    @app.get('/api/notices/unread')
    @auth
    def notice_unread():
        n = db.session.query(func.count(Notice.id)).filter(Notice.user_id == g.user.id, Notice.seen.is_(False)).scalar() or 0
        return jsonify(n=n)

    @app.get('/api/notices')
    @auth
    def notice_list():
        uid = g.user.id; prune(uid)
        rows = Notice.query.filter(Notice.user_id == uid).order_by(Notice.ts.desc(), Notice.id.desc()).limit(50).all()
        ids = list({r.actor_id for r in rows if r.actor_id}) or [0]
        actors = {u.id: u for u in User.query.filter(User.id.in_(ids), User.banned == False).all()}
        refs = list({r.ref for r in rows if r.ref}) or ['-']
        open_rooms = {x for (x,) in db.session.query(Room.id).filter(Room.id.in_(refs), Room.last_active > now() - 90)}
        items = []
        for r in rows:
            if r.actor_id and r.actor_id not in actors: continue      # banned or deleted
            try: info = json.loads(r.data or '{}')
            except ValueError: info = {}
            items.append(dict(id=r.id, kind=r.kind, ts=r.ts, seen=bool(r.seen), data=info,
                              ref=r.ref if r.ref in open_rooms else '',
                              actor=pub(actors[r.actor_id]) if r.actor_id else None))
        return jsonify(items=items)

    @app.post('/api/notices/read')
    @auth
    def notice_read():
        upto = num(body().get('upto'))
        Notice.query.filter(Notice.user_id == g.user.id, Notice.id <= upto).update({'seen': True}, synchronize_session=False)
        db.session.commit()
        return jsonify(ok=1)

    # ---- viewers and likes ----
    def inside(rid, uid):
        p = db.session.get(Presence, (rid, uid))
        return bool(p and p.seen > now() - 30)

    @app.get('/api/rooms/<rid>/viewers')
    @auth
    def room_viewers(rid):
        r = db.session.get(Room, rid)
        if not r: return err('ROOM_GONE', 404)
        if not inside(rid, g.user.id): return err('Join the room first', 409)
        q = db.session.query(User).join(Presence, Presence.user_id == User.id).filter(
            Presence.room_id == rid, Presence.seen > now() - 20, User.banned == False)
        total = q.count()
        us = q.order_by((User.id == r.host_id).desc(), User.spent.desc(), User.id).limit(100).all()
        return jsonify(total=total, users=[pub(u) for u in us])

    def likes(rid):
        return db.session.query(RoomLike.count).filter(RoomLike.room_id == rid).scalar() or 0

    @app.post('/api/rooms/<rid>/like')
    @auth
    def room_like(rid):
        if not db.session.get(Room, rid): return err('ROOM_GONE', 404)
        if not inside(rid, g.user.id): return err('Join the room first', 409)
        if limited('like', 0.8): return err('Slow down', 429)
        n = max(1, min(MAX_LIKE_BATCH, num(body().get('n'), 1)))
        add = update(RoomLike).where(RoomLike.room_id == rid).values(count=RoomLike.count + n)
        if db.session.execute(add, execution_options={'synchronize_session': False}).rowcount != 1:
            try:
                db.session.add(RoomLike(room_id=rid, count=n)); db.session.commit()
            except IntegrityError:      # two first likes at the same moment
                db.session.rollback(); db.session.execute(add, execution_options={'synchronize_session': False})
        db.session.commit()
        return jsonify(likes=likes(rid))

    def drop_room(rid):
        RoomLike.query.filter(RoomLike.room_id == rid).delete(synchronize_session=False)

    # ---- PK history (only finished matches that had two hosts) ----
    @app.get('/api/pk/history')
    @auth
    def pk_history():
        scope = 'all' if request.args.get('scope') == 'all' else 'user'
        uid = num(request.args.get('uid'), g.user.id) or g.user.id
        done = [PKBattle.status == 'ended', PKBattle.host2_id.isnot(None)]
        stats = None
        if scope == 'user':
            done.append(or_(PKBattle.host1_id == uid, PKBattle.host2_id == uid))
            count = lambda *w: db.session.query(func.count(PKBattle.id)).filter(*done, *w).scalar() or 0
            total, wins = count(), count(PKBattle.winner_id == uid)
            draws = count(PKBattle.winner_id.is_(None))
            stats = dict(total=total, wins=wins, draws=draws, losses=total - wins - draws)
        rows = PKBattle.query.filter(*done).order_by(PKBattle.id.desc()).limit(30).all()
        ids = list({i for r in rows for i in (r.host1_id, r.host2_id)}) or [0]
        users = {u.id: u for u in User.query.filter(User.id.in_(ids)).all()}

        def side(i):
            u = users.get(i)
            return pub(u) if u and not u.banned else dict(id=0, name='—', photo='', fr=None)

        return jsonify(uid=uid if scope == 'user' else 0, stats=stats, items=[dict(
            id=r.id, host1=side(r.host1_id), host2=side(r.host2_id), s1=r.host1_gifts or 0, s2=r.host2_gifts or 0,
            winner=r.winner_id, ts=r.ended_at or r.started_at) for r in rows])

    def delete_user(uid):       # runs inside the admin's own transaction
        Notice.query.filter(or_(Notice.user_id == uid, Notice.actor_id == uid)).delete(synchronize_session=False)

    return dict(follow=follow, host_live=host_live, gift=gift, system=system, likes=likes,
                drop_room=drop_room, delete_user=delete_user)
