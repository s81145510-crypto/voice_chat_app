# -*- coding: utf-8 -*-
"""Server-authoritative host activity tracking and first-week rewards.

Only room-host heartbeats are counted. Client elapsed-time values are never accepted.
Activity coins are promo coins: they increase spendable coins but not real_coins,
earnings, or commission, so they are not withdrawable.
"""
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

try:
    from . import rules
except ImportError:
    import rules


def init_host_rewards(app, db, deps):
    User, Room, Presence = deps['User'], deps['Room'], deps['Presence']
    now, tx = deps['now'], deps['tx']
    from flask import g, jsonify

    class HostActivityDay(db.Model):
        __tablename__ = 'host_activity_day'
        user_id = db.Column(db.Integer, primary_key=True)
        day = db.Column(db.String(10), primary_key=True)
        seconds = db.Column(db.Integer, default=0, nullable=False)
        day_number = db.Column(db.Integer, default=0, nullable=False)

    class HostActivityReward(db.Model):
        __tablename__ = 'host_activity_reward'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)
        day = db.Column(db.String(10), nullable=False)
        block = db.Column(db.Integer, nullable=False)
        amount = db.Column(db.Integer, nullable=False)
        ts = db.Column(db.Integer, default=now, nullable=False)
        __table_args__ = (db.UniqueConstraint('user_id', 'day', 'block', name='uq_host_activity_reward'),)

    with app.app_context():
        db.create_all()

    def add_verified_seconds(user_id, start, end):
        """Add at most 20 seconds from one recent server-observed heartbeat."""
        if end <= start:
            return
        start = max(int(start), int(end) - 20)
        cursor = start
        while cursor < end:
            local = datetime.fromtimestamp(cursor, rules.EAT)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            stop = min(end, int(midnight.timestamp()))
            day_key = local.date().isoformat()
            try:
                row = (HostActivityDay.query.filter_by(user_id=user_id, day=day_key)
                       .with_for_update().first())
                if row is None:
                    prior_days = HostActivityDay.query.filter(
                        HostActivityDay.user_id == user_id,
                        HostActivityDay.seconds > 0
                    ).count()
                    row = HostActivityDay(user_id=user_id, day=day_key, seconds=0,
                                          day_number=prior_days + 1)
                    db.session.add(row)
                    db.session.flush()
                row.seconds = int(row.seconds or 0) + (stop - cursor)
                eligible_blocks = min(rules.MAX_REWARDS_PER_DAY, row.seconds // rules.BLOCK_SECONDS)
                # The first seven active hosting days have explicit welcome amounts.
                # Later tiers require an authoritative host-level progression model;
                # do not guess a level from spending or award the wrong amount.
                while row.day_number <= len(rules.WELCOME_REWARDS) and row.rewarded_blocks if hasattr(row, 'rewarded_blocks') else False:
                    break
                current_blocks = HostActivityReward.query.filter_by(
                    user_id=user_id, day=day_key
                ).count()
                for block in range(current_blocks + 1, eligible_blocks + 1):
                    if row.day_number > len(rules.WELCOME_REWARDS):
                        break
                    reward_key = (user_id, day_key, block)
                    if HostActivityReward.query.filter_by(
                        user_id=user_id, day=day_key, block=block
                    ).first():
                        continue
                    amount = rules.reward_per_block(row.day_number, 1)
                    db.session.add(HostActivityReward(user_id=user_id, day=day_key,
                                                      block=block, amount=amount))
                    db.session.flush()
                    # Promo balance only. Never increase real_coins or withdrawable earnings.
                    db.session.query(User).filter(User.id == user_id).update(
                        {User.coins: User.coins + amount}, synchronize_session=False)
                    tx(user_id, 'host_activity', amount,
                       f'verified hosting day {row.day_number}, block {block}')
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            except Exception:
                db.session.rollback()
                raise
            cursor = stop

    def heartbeat(room_id, user_id, previous_seen, current_ts):
        if not previous_seen or current_ts - previous_seen < 8 or current_ts - previous_seen > 30:
            return
        room = db.session.get(Room, room_id)
        user = db.session.get(User, user_id)
        if not room or not user or room.host_id != user_id:
            return
        if user.banned or user.host_status != 'host':
            return
        # Presence must still be fresh and belong to this host.
        presence = db.session.get(Presence, (room_id, user_id))
        if not presence or current_ts - presence.seen > 5:
            return
        add_verified_seconds(user_id, previous_seen, current_ts)

    @app.get('/api/creator/activity')
    @deps['auth']
    def creator_activity():
        day = datetime.fromtimestamp(now(), rules.EAT).date().isoformat()
        row = db.session.get(HostActivityDay, (g.user.id, day))
        rewards = HostActivityReward.query.filter_by(user_id=g.user.id, day=day).order_by(
            HostActivityReward.block.asc()).all()
        return jsonify(day=day, verified_seconds=int(row.seconds if row else 0),
                       eligible_blocks=min(rules.MAX_REWARDS_PER_DAY,
                                           int((row.seconds if row else 0) // rules.BLOCK_SECONDS)),
                       rewards=[dict(block=r.block, coins=r.amount, ts=r.ts) for r in rewards],
                       reward_coins=sum(r.amount for r in rewards),
                       withdrawable=False,
                       note='Only verified host heartbeats count. First seven active hosting days use the welcome schedule; later rewards wait for an authoritative host-level system.')

    return dict(heartbeat=heartbeat)


