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

    def test_gift_journal_rejects_overallocated_or_invalid_amounts(self):
        with self.assertRaisesRegex(ValueError, "allocation exceeds"):
            JOURNAL["gift_lines"](1, 10, 10, 8, commission_total=3)
        with self.assertRaisesRegex(ValueError, "whole integers"):
            JOURNAL["gift_lines"](1, 10, True, 0)

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


if __name__ == "__main__":
    unittest.main()
