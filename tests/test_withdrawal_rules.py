"""Regression tests for the agreed Telebirr withdrawal rules."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import rules


class WithdrawalQuoteTests(unittest.TestCase):
    def test_reference_rate_and_minimum(self):
        quote = rules.withdrawal_quote(10_000)
        self.assertEqual(quote["birr"], 1_000)
        self.assertEqual(quote["rate"], "0.10")

    def test_minimum_is_exactly_ten_thousand_coins(self):
        self.assertEqual(rules.MIN_WITHDRAW_ETB, 1_000)
        self.assertEqual(rules.MIN_WITHDRAW_COINS, 10_000)
        with self.assertRaisesRegex(ValueError, "minimum withdrawal"):
            rules.withdrawal_quote(9_999)

    def test_invalid_coin_amounts_are_rejected(self):
        for value in (0, -1, True, 10_000.0, "10000"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    rules.withdrawal_quote(value)


class WithdrawalSourceSplitTests(unittest.TestCase):
    def test_exact_half_gift_and_remaining_sources(self):
        split = rules.withdrawal_source_split(
            coins=10_000, gift_earnings=5_000, pk_earnings=3_000, commission=2_000
        )
        self.assertEqual(split, {
            "gift_earnings": 5_000,
            "pk_earnings": 3_000,
            "earnings": 8_000,
            "commission": 2_000,
        })

    def test_more_than_half_gift_is_used_when_required(self):
        split = rules.withdrawal_source_split(
            coins=10_000, gift_earnings=9_000, pk_earnings=1_000, commission=1_000
        )
        self.assertEqual(split["gift_earnings"], 8_000)
        self.assertEqual(split["commission"], 1_000)
        self.assertEqual(split["pk_earnings"], 1_000)

    def test_rejects_less_than_half_genuine_gift_earnings(self):
        with self.assertRaisesRegex(ValueError, "at least 50%"):
            rules.withdrawal_source_split(10_000, 4_999, 10_000, 10_000)

    def test_rejects_insufficient_total_withdrawable_balance(self):
        with self.assertRaisesRegex(ValueError, "not enough withdrawable"):
            rules.withdrawal_source_split(10_000, 5_000, 2_000, 2_000)

    def test_rejects_negative_and_boolean_balances(self):
        for value in (-1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    rules.withdrawal_source_split(10_000, 5_000, value, 5_000)


class WithdrawalStateTests(unittest.TestCase):
    def test_only_expected_forward_transitions_are_allowed(self):
        expected = {
            ("pending", "under_review"): "under_review",
            ("pending", "reject"): "rejected",
            ("under_review", "approve"): "approved",
            ("under_review", "reject"): "rejected",
            ("approved", "processing"): "processing",
            ("approved", "reject"): "rejected",
            ("processing", "paid"): "paid",
        }
        for pair, status in expected.items():
            with self.subTest(pair=pair):
                self.assertEqual(rules.withdrawal_next_status(*pair), status)

    def test_paid_and_rejected_are_terminal_in_current_workflow(self):
        for current in ("paid", "rejected"):
            for action in ("approve", "processing", "paid", "reject", "reverse", "fail"):
                with self.subTest(current=current, action=action):
                    self.assertIsNone(rules.withdrawal_next_status(current, action))

    def test_cannot_skip_review_or_mark_unprocessed_request_paid(self):
        self.assertIsNone(rules.withdrawal_next_status("pending", "approve"))
        self.assertIsNone(rules.withdrawal_next_status("pending", "paid"))
        self.assertIsNone(rules.withdrawal_next_status("under_review", "paid"))


if __name__ == "__main__":
    unittest.main()
