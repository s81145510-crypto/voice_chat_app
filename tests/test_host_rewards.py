import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import rules


class HostRewardSafety(unittest.TestCase):
    def test_welcome_schedule_exact(self):
        self.assertEqual([rules.reward_per_block(day, 1) for day in range(1, 8)],
                         [100, 120, 140, 160, 180, 200, 250])

    def test_two_hour_daily_cap(self):
        self.assertEqual(rules.rewards_earned(2 * 3600), 2)
        self.assertEqual(rules.rewards_earned(12 * 3600), 2)

    def test_under_one_hour_no_reward(self):
        self.assertEqual(rules.rewards_earned(3599), 0)


if __name__ == '__main__':
    unittest.main()
