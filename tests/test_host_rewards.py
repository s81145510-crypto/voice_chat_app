import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import rules


class HostRewardRules(unittest.TestCase):
    def test_welcome_schedule_exact(self):
        self.assertEqual([rules.reward_per_block(day, 1) for day in range(1, 8)],
                         [100, 120, 140, 160, 180, 200, 250])

    def test_level_schedule_exact(self):
        self.assertEqual([rules.reward_per_block(8, level) for level in (1, 5, 6, 10, 11, 20, 21, 30, 31, 50, 51)],
                         [50, 50, 65, 65, 80, 80, 100, 100, 125, 125, 150])

    def test_two_hour_daily_cap(self):
        self.assertEqual(rules.rewards_earned(2 * 3600), 2)
        self.assertEqual(rules.rewards_earned(12 * 3600), 2)

    def test_under_one_hour_no_reward(self):
        self.assertEqual(rules.rewards_earned(3599), 0)

    def test_overlapping_intervals_are_not_double_counted(self):
        self.assertEqual(rules.merge_intervals([(100, 200), (150, 250), (250, 300)]), [(100, 300)])

    def test_interval_splits_at_ethiopian_midnight(self):
        # 20:59:50 UTC is 23:59:50 in Ethiopia; next 20 seconds cross midnight.
        start = int(datetime(2026, 1, 1, 20, 59, 50, tzinfo=timezone.utc).timestamp())
        self.assertEqual(rules.eligible_seconds_by_day([(start, start + 20)]),
                         {'2026-01-01': 10, '2026-01-02': 10})


class HostRewardService(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from flask_sqlalchemy import SQLAlchemy
        from backend.host_rewards import init_host_rewards

        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        self.db = SQLAlchemy(self.app)

        class User(self.db.Model):
            id = self.db.Column(self.db.Integer, primary_key=True)
            coins = self.db.Column(self.db.Integer, default=0, nullable=False)
            banned = self.db.Column(self.db.Boolean, default=False)
            host_status = self.db.Column(self.db.String(8), default='host')

        class Room(self.db.Model):
            id = self.db.Column(self.db.String(16), primary_key=True)
            host_id = self.db.Column(self.db.Integer)

        class Presence(self.db.Model):
            room_id = self.db.Column(self.db.String(16), primary_key=True)
            user_id = self.db.Column(self.db.Integer, primary_key=True)
            seen = self.db.Column(self.db.Integer)

        self.User, self.Room, self.Presence = User, Room, Presence
        self.current = int(datetime(2026, 5, 1, 9, 0, tzinfo=rules.EAT).timestamp())
        self.tx_rows = []
        def tx(uid, kind, amount, note=''):
            self.tx_rows.append((uid, kind, amount, note))
        self.service = init_host_rewards(
            self.app, self.db,
            dict(User=User, Room=Room, Presence=Presence,
                 now=lambda: self.current, tx=tx, auth=lambda f: f)
        )
        with self.app.app_context():
            self.db.session.add(User(id=1, coins=0, banned=False, host_status='host'))
            self.db.session.add(Room(id='room1', host_id=1))
            self.db.session.add(Presence(room_id='room1', user_id=1, seen=self.current))
            self.db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            self.db.session.remove()
            self.db.engine.dispose()

    def _host_for(self, seconds, user_id=1):
        with self.app.app_context():
            start = self.current
            for _ in range(seconds // 20):
                previous = self.current
                self.current += 20
                presence = self.db.session.get(self.Presence, ('room1', user_id))
                presence.seen = self.current
                self.db.session.commit()
                self.service['heartbeat']('room1', user_id, previous, self.current)
            return start

    def test_verified_hour_issues_first_welcome_reward_once(self):
        with self.app.app_context():
            self._host_for(3600)
            user = self.db.session.get(self.User, 1)
            self.assertEqual(user.coins, 100)
            self.assertEqual(len([x for x in self.tx_rows if x[1] == 'host_activity']), 1)

    def test_daily_reward_limit_is_two(self):
        with self.app.app_context():
            self._host_for(3 * 3600)
            user = self.db.session.get(self.User, 1)
            self.assertEqual(user.coins, 200)
            self.assertEqual(len([x for x in self.tx_rows if x[1] == 'host_activity']), 2)

    def test_non_host_or_banned_user_gets_no_reward(self):
        with self.app.app_context():
            user = self.db.session.get(self.User, 1)
            user.host_status = 'none'
            self.db.session.commit()
            self._host_for(3600)
            self.assertEqual(self.db.session.get(self.User, 1).coins, 0)
            user.host_status = 'host'
            user.banned = True
            self.db.session.commit()
            self._host_for(3600)
            self.assertEqual(self.db.session.get(self.User, 1).coins, 0)


if __name__ == '__main__':
    unittest.main()
