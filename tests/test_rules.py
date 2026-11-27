import os, sys, random, unittest
from datetime import datetime
from decimal import Decimal
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import rules as r

CHAIN = [11, 12, 13, 14, 15, 16, 17]
def at(day, h, m=0): return int(datetime(2026, 12, day, h, m, tzinfo=r.EAT).timestamp())


class Gifts(unittest.TestCase):
    def test1_2_all_seven_generations(self):          # 1,000 ETB in santim
        x = r.split_gift(100000, CHAIN)
        self.assertEqual((x['host'], x['commission_total'], x['reserve'], x['platform']), (50000, 12000, 0, 38000))
        self.assertEqual([c[2] for c in x['commissions']], [5000, 2500, 1500, 1000, 750, 750, 500])

    def test3_no_generation_goes_to_reserve(self):
        x = r.split_gift(100000)
        self.assertEqual((x['host'], x['commission_total'], x['reserve'], x['platform']), (50000, 0, 12000, 38000))

    def test_ineligible_share_is_not_moved(self):
        x = r.split_gift(100000, CHAIN, eligible=lambda u: u != 12)
        self.assertEqual(x['reserve'], 2500)
        self.assertEqual(x['commissions'][1], (3, 13, 1500))

    def test_never_over_allocates(self):
        rnd = random.Random(21)
        for v in list(range(0, 400)) + [rnd.randrange(10**9) for _ in range(3000)]:
            x = r.split_gift(v, CHAIN[:rnd.randrange(8)])
            self.assertEqual(x['host'] + x['commission_total'] + x['reserve'] + x['platform'], v)
            self.assertLessEqual(Decimal(x['commission_total'] + x['reserve']), v * r.POOL_RATE)
            self.assertGreaterEqual(x['platform'], 0)

    def test_loops_and_bad_input(self):
        parents = {1: 2, 2: 3, 3: 1}
        self.assertEqual(r.upline(1, parents.get), [2, 3])
        self.assertEqual(r.upline(5, {5: 5}.get), [])
        for bad in (-1, 1.5, True):
            self.assertRaises(ValueError, r.split_gift, bad)
        self.assertRaises(ValueError, r.split_gift, 100, [2, 2])


class Activity(unittest.TestCase):
    def test4_welcome_total(self):
        self.assertEqual(sum(2 * r.reward_per_block(d, 1) for d in range(1, 8)), 2300)

    def test_levels_after_day_seven(self):
        got = [r.reward_per_block(8, lv) for lv in (1, 5, 6, 10, 11, 20, 21, 30, 31, 50, 51, 90)]
        self.assertEqual(got, [50, 50, 65, 65, 80, 80, 100, 100, 125, 125, 150, 150])

    def test5_overlap_counts_once(self):
        days = r.eligible_seconds_by_day([(at(1, 10), at(1, 11)), (at(1, 10, 30), at(1, 11, 30))])
        self.assertEqual(days, {'2026-12-01': 5400})
        self.assertEqual(r.rewards_earned(days['2026-12-01']), 1)

    def test5_daily_cap_and_short_time(self):
        self.assertEqual(r.rewards_earned(3599), 0)
        self.assertEqual(r.rewards_earned(9 * 3600), 2)

    def test5_midnight_in_ethiopia(self):
        days = r.eligible_seconds_by_day([(at(1, 23, 30), at(2, 0, 45))])
        self.assertEqual(days, {'2026-12-01': 1800, '2026-12-02': 2700})


class Exchange(unittest.TestCase):
    def test6_fee_and_recorded_rate(self):
        q = r.exchange_quote(100000, '2.5')
        self.assertEqual((q['fee'], q['net'], q['coins'], q['rate']), (5000, 95000, 2375, '2.5'))
        self.assertEqual(q['fee'] + q['net'], q['gross'])

    def test6_bad_values(self):
        for rate in ('0', '-1', 'NaN', 'Infinity'):
            self.assertRaises(ValueError, r.exchange_quote, 1000, rate)
        self.assertRaises(TypeError, r.exchange_quote, 1000, 2.5)
        self.assertRaises(ValueError, r.exchange_quote, 0, '2')
        self.assertRaises(ValueError, r.exchange_quote, 1, '2')


if __name__ == '__main__':
    unittest.main()
