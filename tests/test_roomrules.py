import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import roomrules as R


class Entry(unittest.TestCase):
    def test_open_room(self):
        self.assertIsNone(R.entry_problem(R.USER, False, False, False))

    def test_private_needs_pass(self):
        self.assertEqual(R.entry_problem(R.USER, False, True, False), 'locked')
        self.assertIsNone(R.entry_problem(R.USER, False, True, True))

    def test_block_beats_password(self):
        self.assertEqual(R.entry_problem(R.USER, True, True, True), 'banned')
        self.assertEqual(R.entry_problem(R.ROOM_ADMIN, True, False, False), 'banned')

    def test_owner_and_staff_always_enter(self):
        for lvl in (R.HOST, R.MOD, R.ADMIN):
            self.assertIsNone(R.entry_problem(lvl, True, True, False))


class Actions(unittest.TestCase):
    def ok(self, *a, **k): self.assertIsNone(R.action_problem(*a, **k))
    def no(self, *a, **k): self.assertIsNotNone(R.action_problem(*a, **k))

    def test_normal_user_can_do_nothing(self):
        for action in R.ACTIONS: self.no(action, R.USER, R.USER, False)

    def test_nobody_acts_on_owner(self):
        for actor in (R.ROOM_ADMIN, R.HOST, R.MOD, R.ADMIN):
            for action in R.ACTIONS: self.no(action, actor, R.USER, True)

    def test_only_downwards(self):
        for actor in (R.ROOM_ADMIN, R.HOST, R.MOD, R.ADMIN):
            for target in range(actor, R.ADMIN + 1):
                for action in ('mute', 'unmute', 'kick', 'ban', 'unban'): self.no(action, actor, target, False)

    def test_room_admin_limits(self):
        for m in (10, 60, 1440): self.ok('mute', R.ROOM_ADMIN, R.USER, False, 0, m)
        self.no('mute', R.ROOM_ADMIN, R.USER, False, 0, 0)
        self.ok('kick', R.ROOM_ADMIN, R.USER, False)
        for action in ('ban', 'unban', 'admin', 'unadmin'): self.no(action, R.ROOM_ADMIN, R.USER, False)

    def test_cannot_undo_someone_above(self):
        self.no('unmute', R.ROOM_ADMIN, R.USER, False, set_by=R.HOST)
        self.no('mute', R.ROOM_ADMIN, R.USER, False, set_by=R.HOST)     # no shortening
        self.no('unban', R.HOST, R.USER, False, set_by=R.MOD)
        self.ok('unban', R.HOST, R.USER, False, set_by=R.HOST)
        self.ok('unmute', R.HOST, R.USER, False, set_by=R.ROOM_ADMIN)
        self.ok('unban', R.ADMIN, R.USER, False, set_by=R.MOD)

    def test_choosing_room_admins(self):
        self.ok('admin', R.HOST, R.USER, False)
        self.ok('unadmin', R.HOST, R.ROOM_ADMIN, False)
        self.ok('admin', R.ADMIN, R.USER, False)
        self.no('admin', R.MOD, R.USER, False)
        self.no('admin', R.HOST, R.MOD, False)

    def test_moderator_is_still_owner_of_own_room(self):
        self.ok('admin', R.MOD, R.USER, False, owner=True)
        self.ok('unadmin', R.MOD, R.ROOM_ADMIN, False, owner=True)

    def test_seat_buttons(self):
        self.assertIsNone(R.seat_problem('mute', R.ROOM_ADMIN, R.USER, False))
        self.assertIsNone(R.seat_problem('kick', R.ROOM_ADMIN, R.USER, False))
        self.assertIsNotNone(R.seat_problem('unmute', R.ROOM_ADMIN, R.USER, False))
        self.assertIsNone(R.seat_problem('unmute', R.HOST, R.USER, False))
        self.assertIsNotNone(R.seat_problem('mute', R.USER, R.USER, False))
        self.assertIsNotNone(R.seat_problem('kick', R.HOST, R.MOD, False))
        self.assertIsNotNone(R.seat_problem('mute', R.ADMIN, R.USER, True))

    def test_bad_input(self):
        self.no('delete', R.ADMIN, R.USER, False)
        self.no('mute', R.HOST, R.USER, False, 0, 7)
        self.no('mute', R.HOST, R.USER, False, 0, -10)

    def test_times_and_levels(self):
        self.assertEqual(R.until(1000, 10), 1600)
        self.assertEqual(R.until(1000, 0), R.FOREVER)
        self.assertEqual(R.level(True, False, True, True), R.ADMIN)
        self.assertEqual(R.level(False, True, True, False), R.MOD)
        self.assertEqual(R.level(False, False, True, True), R.HOST)
        self.assertEqual(R.level(False, False, False, True), R.ROOM_ADMIN)


if __name__ == '__main__':
    unittest.main()
