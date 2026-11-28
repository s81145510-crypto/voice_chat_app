"""Regression tests for the append-only balanced journal foundation."""
import os
import tempfile
import unittest

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-journal-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DATA, "journal.db")
os.environ["SECRET_KEY"] = "journal-test-only-secret"

from backend.main import app, db, JOURNAL


class JournalValidationTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        JOURNAL["JournalLine"].query.delete()
        JOURNAL["JournalEntry"].query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        self.ctx.pop()

    def lines(self, debit=100, credit=100):
        return [
            {"account_code": "CASH_CLEARING", "debit": debit, "credit": 0, "currency": "ETB"},
            {"account_code": "COIN_LIABILITY", "debit": 0, "credit": credit, "currency": "ETB"},
        ]

    def test_balanced_entry_posts_and_totals_match(self):
        entry, duplicate = JOURNAL["post_internal"](
            "test_purchase", "purchase-1001", "journal-test-balanced-0001",
            "ETB", "Test purchase", self.lines()
        )
        db.session.commit()
        saved = JOURNAL["serialize"](entry)
        self.assertFalse(duplicate)
        self.assertEqual(saved["total_debit"], 100)
        self.assertEqual(saved["total_credit"], 100)
        self.assertEqual(len(saved["lines"]), 2)

    def test_unbalanced_entry_is_rejected_before_insert(self):
        with self.assertRaisesRegex(ValueError, "unbalanced journal"):
            JOURNAL["post_internal"](
                "test_purchase", "purchase-1002", "journal-test-unbalanced-001",
                "ETB", "Unbalanced", self.lines(100, 99)
            )
        self.assertEqual(JOURNAL["JournalEntry"].query.count(), 0)

    def test_boolean_fraction_negative_and_two_sided_lines_are_rejected(self):
        invalid_lines = [
            self.lines(True, 100),
            self.lines(10.5, 10.5),
            self.lines(-1, -1),
            [
                {"account_code": "CASH_CLEARING", "debit": 10, "credit": 10, "currency": "ETB"},
                {"account_code": "COIN_LIABILITY", "debit": 0, "credit": 20, "currency": "ETB"},
            ],
        ]
        for lines in invalid_lines:
            with self.subTest(lines=lines):
                with self.assertRaises(ValueError):
                    JOURNAL["post_internal"](
                        "test_purchase", "purchase-invalid", "journal-test-invalid-0001",
                        "ETB", "Invalid line", lines
                    )
                db.session.rollback()

    def test_mixed_currency_is_rejected(self):
        lines = self.lines()
        lines[1]["currency"] = "USD"
        with self.assertRaisesRegex(ValueError, "all lines"):
            JOURNAL["post_internal"](
                "test_purchase", "purchase-1003", "journal-test-currency-001",
                "ETB", "Mixed currency", lines
            )

    def test_identical_idempotent_retry_returns_existing_entry(self):
        first, duplicate1 = JOURNAL["post_internal"](
            "test_purchase", "purchase-1004", "journal-test-idempotent-001",
            "ETB", "Retry-safe purchase", self.lines()
        )
        db.session.commit()
        second, duplicate2 = JOURNAL["post_internal"](
            "test_purchase", "purchase-1004", "journal-test-idempotent-001",
            "ETB", "Retry-safe purchase", self.lines()
        )
        self.assertFalse(duplicate1)
        self.assertTrue(duplicate2)
        self.assertEqual(first.id, second.id)
        self.assertEqual(JOURNAL["JournalEntry"].query.count(), 1)

    def test_reused_idempotency_key_with_changed_payload_is_rejected(self):
        JOURNAL["post_internal"](
            "test_purchase", "purchase-1005", "journal-test-conflict-001",
            "ETB", "Original", self.lines()
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "idempotency key conflict"):
            JOURNAL["post_internal"](
                "test_purchase", "purchase-1005", "journal-test-conflict-001",
                "ETB", "Changed", self.lines(200, 200)
            )

    def test_caller_rollback_removes_entry_and_lines_together(self):
        entry, _ = JOURNAL["post_internal"](
            "test_purchase", "purchase-1006", "journal-test-rollback-001",
            "ETB", "Will rollback", self.lines()
        )
        entry_id = entry.id
        db.session.rollback()
        self.assertIsNone(db.session.get(JOURNAL["JournalEntry"], entry_id))
        self.assertEqual(JOURNAL["JournalLine"].query.count(), 0)

    def test_normal_gift_journal_allocation_balances_purchased_and_promo_coins(self):
        lines = JOURNAL["gift_lines"](sender_id=11, total=100, real_used=60,
                                      host_earning=30, commission_total=6)
        self.assertEqual(sum(x["debit"] for x in lines), 100)
        self.assertEqual(sum(x["credit"] for x in lines), 100)
        self.assertEqual({x["account_code"] for x in lines},
                         {"liability.user_coin_balance", "liability.promo_coin_balance",
                          "liability.host_earnings", "liability.agent_commission",
                          "revenue.platform_gift"})

    def test_lucky_gift_journal_balances_gross_spend_and_multiplier_payout(self):
        lines = JOURNAL["gift_lines"](sender_id=12, total=50, real_used=40,
                                      host_earning=0, lucky_refund=100)
        self.assertEqual(sum(x["debit"] for x in lines), 150)
        self.assertEqual(sum(x["credit"] for x in lines), 150)
        self.assertIn("expense.lucky_payout", {x["account_code"] for x in lines})
        self.assertNotIn("liability.host_earnings", {x["account_code"] for x in lines})

    def test_lucky_gift_with_zero_multiplier_is_still_booked_as_lucky(self):
        lines = JOURNAL["gift_lines"](sender_id=13, total=20, real_used=20,
                                      host_earning=0, is_lucky=True)
        self.assertEqual(sum(x["debit"] for x in lines), 20)
        self.assertEqual(sum(x["credit"] for x in lines), 20)
        self.assertIn("revenue.lucky_gift_sales", {x["account_code"] for x in lines})
        self.assertNotIn("revenue.platform_gift", {x["account_code"] for x in lines})

    def test_commission_carry_release_can_exceed_current_gift_pool(self):
        lines = JOURNAL["gift_lines"](sender_id=14, total=1, real_used=1,
                                      host_earning=0, commission_total=3)
        self.assertEqual(sum(x["debit"] for x in lines), 4)
        self.assertEqual(sum(x["credit"] for x in lines), 4)
        self.assertIn("expense.commission_carry_release",
                      {x["account_code"] for x in lines})

    def test_gift_journal_rejects_overallocated_or_invalid_amounts(self):
        with self.assertRaisesRegex(ValueError, "allocation exceeds"):
            JOURNAL["gift_lines"](1, 10, 9, 10, commission_total=0)
        with self.assertRaisesRegex(ValueError, "whole integers"):
            JOURNAL["gift_lines"](1, 10, True, 0)

    def test_pk_gift_separates_purchased_prize_pool_and_promo_spend(self):
        lines = JOURNAL["pk_gift_lines"](sender_id=21, total=100, real_used=70)
        self.assertEqual(sum(x["debit"] for x in lines), 100)
        self.assertEqual(sum(x["credit"] for x in lines), 100)
        self.assertEqual({x["account_code"] for x in lines},
                         {"liability.user_coin_balance", "liability.pk_prize_pool",
                          "liability.promo_coin_balance", "revenue.platform_pk_gift"})

    def test_pk_gift_with_only_promo_coins_does_not_fund_prize_pool(self):
        lines = JOURNAL["pk_gift_lines"](sender_id=22, total=25, real_used=0)
        self.assertEqual(sum(x["debit"] for x in lines), 25)
        self.assertEqual(sum(x["credit"] for x in lines), 25)
        self.assertNotIn("liability.pk_prize_pool", {x["account_code"] for x in lines})

    def test_pk_payout_settles_pool_and_retains_unpaid_share(self):
        lines = JOURNAL["pk_payout_lines"](100, [(31, 70, "pk_win"), (32, 20, "pk_lose")])
        self.assertEqual(sum(x["debit"] for x in lines), 100)
        self.assertEqual(sum(x["credit"] for x in lines), 100)
        self.assertIn("revenue.platform_pk", {x["account_code"] for x in lines})

    def test_pk_payout_without_second_host_keeps_missing_share_with_platform(self):
        lines = JOURNAL["pk_payout_lines"](100, [(31, 70, "pk_win"), (None, 20, "pk_lose")])
        self.assertEqual(sum(x["debit"] for x in lines), 100)
        self.assertEqual(sum(x["credit"] for x in lines), 100)
        platform = [x for x in lines if x["account_code"] == "revenue.platform_pk"]
        self.assertEqual(platform[0]["credit"], 30)

    def test_pk_payout_rejects_overpayment(self):
        with self.assertRaisesRegex(ValueError, "exceed prize pool"):
            JOURNAL["pk_payout_lines"](100, [(31, 80, "pk_win"), (32, 30, "pk_lose")])

    def test_invalid_account_and_invalid_idempotency_key_are_rejected(self):
        lines = self.lines()
        lines[0]["account_code"] = "bad account!"
        with self.assertRaisesRegex(ValueError, "invalid account_code"):
            JOURNAL["post_internal"](
                "test_purchase", "purchase-1007", "journal-test-account-001",
                "ETB", "Bad account", lines
            )
        with self.assertRaisesRegex(ValueError, "invalid journal idempotency key"):
            JOURNAL["post_internal"](
                "test_purchase", "purchase-1007", "bad key",
                "ETB", "Bad key", self.lines()
            )


    def test_withdrawal_reserve_moves_earnings_and_commission_to_pending(self):
        lines = JOURNAL["withdrawal_reserve_lines"](41, 300, 200)
        self.assertEqual(sum(x["debit"] for x in lines), 500)
        self.assertEqual(sum(x["credit"] for x in lines), 500)
        self.assertEqual({x["account_code"] for x in lines},
                         {"liability.host_earnings", "liability.agent_commission",
                          "liability.withdrawal_pending"})

    def test_rejected_withdrawal_restores_original_liability_buckets(self):
        lines = JOURNAL["withdrawal_release_lines"](42, 350, 150)
        self.assertEqual(sum(x["debit"] for x in lines), 500)
        self.assertEqual(sum(x["credit"] for x in lines), 500)
        self.assertEqual(lines[0]["account_code"], "liability.withdrawal_pending")
        self.assertEqual({x["account_code"] for x in lines[1:]},
                         {"liability.host_earnings", "liability.agent_commission"})

    def test_withdrawal_helpers_reject_invalid_amounts(self):
        with self.assertRaisesRegex(ValueError, "whole integers"):
            JOURNAL["withdrawal_reserve_lines"](43, True, 10)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            JOURNAL["withdrawal_release_lines"](43, 0, 0)


if __name__ == "__main__":
    unittest.main()
