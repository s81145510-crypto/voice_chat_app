import unittest
from backend import rules


class WithdrawalPolicyTests(unittest.TestCase):
    def test_reference_rate_and_minimum(self):
        self.assertEqual(rules.MIN_WITHDRAW_COINS, 10000)
        self.assertEqual(rules.withdrawal_quote(10000),
                         {"coins": 10000, "birr": 1000, "rate": "0.10"})

    def test_below_minimum_is_rejected(self):
        with self.assertRaises(ValueError):
            rules.withdrawal_quote(9999)

    def test_genuine_gift_earnings_must_be_at_least_half(self):
        with self.assertRaisesRegex(ValueError, "50%"):
            rules.withdrawal_source_split(10000, 4999, 6000)

    def test_split_keeps_at_least_half_from_gift_earnings(self):
        split = rules.withdrawal_source_split(10001, 6000, 5000)
        self.assertEqual(split, {"earnings": 5001, "commission": 5000})
        self.assertGreaterEqual(split["earnings"], (10001 + 1) // 2)

    def test_not_enough_total_withdrawable_balance(self):
        with self.assertRaisesRegex(ValueError, "not enough"):
            rules.withdrawal_source_split(10000, 6000, 3000)

    def test_withdrawal_state_machine_rejects_skipped_steps(self):
        self.assertEqual(rules.withdrawal_next_status("pending", "under_review"), "under_review")
        self.assertEqual(rules.withdrawal_next_status("under_review", "approve"), "approved")
        self.assertEqual(rules.withdrawal_next_status("approved", "processing"), "processing")
        self.assertEqual(rules.withdrawal_next_status("processing", "paid"), "paid")
        self.assertIsNone(rules.withdrawal_next_status("pending", "paid"))
        self.assertIsNone(rules.withdrawal_next_status("under_review", "paid"))
        self.assertIsNone(rules.withdrawal_next_status("paid", "reject"))

    def test_rejection_allowed_before_payout_processing_only(self):
        for status in ("pending", "under_review", "approved"):
            self.assertEqual(rules.withdrawal_next_status(status, "reject"), "rejected")
        self.assertIsNone(rules.withdrawal_next_status("processing", "reject"))

    def test_activity_and_promo_coins_are_not_cashout_sources(self):
        with self.assertRaises(ValueError):
            rules.withdrawal_source_split(10000, 4000, 5000)


if __name__ == "__main__":
    unittest.main()
