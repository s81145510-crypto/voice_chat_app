"""Import-time smoke tests for the Flask API and the new PWA endpoints."""
import os
import tempfile
import unittest

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-party-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DATA, "smoke.db")
os.environ["SECRET_KEY"] = "test-only-secret"

from backend.main import app, db, FIN, strict_num


class AppSmokeTests(unittest.TestCase):
    def test_strict_num_rejects_fractional_and_boolean_inputs(self):
        self.assertEqual(strict_num(12), 12)
        self.assertEqual(strict_num("12"), 12)
        self.assertEqual(strict_num(" 12 "), 12)
        self.assertIsNone(strict_num(12.5))
        self.assertIsNone(strict_num(12.0))
        self.assertIsNone(strict_num(True))
        self.assertIsNone(strict_num("12.5"))
        self.assertIsNone(strict_num(""))

    def test_app_imports_and_new_routes_are_registered(self):
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/api/notifications", rules)
        self.assertIn("/api/notifications/read", rules)
        self.assertIn("/api/rooms/<rid>/audience", rules)
        self.assertIn("/api/pk/history", rules)
        self.assertIn("/api/users/<int:uid>/block", rules)
        self.assertIn("/api/users/<int:uid>/report", rules)
        self.assertIn("/api/blocks", rules)
        self.assertIn("/api/admin/reports", rules)
        self.assertIn("/api/admin/report", rules)
        self.assertIn("/api/creator/dashboard", rules)

    def test_notification_table_is_created(self):
        from sqlalchemy import inspect
        with app.app_context():
            self.assertTrue(inspect(db.engine).has_table("app_notification"))
            self.assertTrue(inspect(db.engine).has_table("user_block"))
            self.assertTrue(inspect(db.engine).has_table("user_report"))
            self.assertIn("gift_earnings", {c["name"] for c in inspect(db.engine).get_columns("user")})
            self.assertIn("payout_reference", {c["name"] for c in inspect(db.engine).get_columns("withdraw")})

    def test_room_gift_idempotency_key_is_unique_and_lookupable(self):
        from sqlalchemy.exc import IntegrityError
        with app.app_context():
            idem = "room:991:smoke-test-req-001"
            try:
                gid, _ = FIN["record_gift"](991, 992, "rose", 1, 10, 10, 5,
                                            "smoke-room", "common", idem)
                found = FIN["find_gift_idem"](idem)
                self.assertIsNotNone(found)
                self.assertEqual(found["sender_id"], 991)
                self.assertEqual(found["receiver_id"], 992)
                self.assertEqual(found["gift_key"], "rose")
                self.assertEqual(found["qty"], 1)
                self.assertEqual(found["room_id"], "smoke-room")
                self.assertEqual(found["total"], 10)
                with self.assertRaises(IntegrityError):
                    FIN["record_gift"](991, 992, "rose", 1, 10, 10, 5,
                                       "smoke-room", "common", idem)
                db.session.rollback()
            finally:
                db.session.rollback()

    def test_lucky_gift_outcome_is_durable_and_unique_per_gift_transaction(self):
        from backend.main import LuckyGiftOutcome
        from sqlalchemy.exc import IntegrityError
        with app.app_context():
            from sqlalchemy import inspect
            self.assertTrue(inspect(db.engine).has_table('lucky_gift_outcome'))
            db.session.add(LuckyGiftOutcome(gift_tx_id=99001, multiplier=5, refund=250))
            db.session.commit()
            try:
                row = LuckyGiftOutcome.query.filter_by(gift_tx_id=99001).first()
                self.assertEqual(row.multiplier, 5)
                self.assertEqual(row.refund, 250)
                db.session.add(LuckyGiftOutcome(gift_tx_id=99001, multiplier=2, refund=100))
                with self.assertRaises(IntegrityError):
                    db.session.commit()
            finally:
                db.session.rollback()
                LuckyGiftOutcome.query.filter_by(gift_tx_id=99001).delete()
                db.session.commit()

    def test_withdraw_request_table_and_unique_idempotency_constraint(self):
        from backend.main import WithdrawRequest
        from sqlalchemy.exc import IntegrityError
        with app.app_context():
            from sqlalchemy import inspect
            self.assertTrue(inspect(db.engine).has_table('withdraw_request'))
            db.session.add(WithdrawRequest(sender_id=7101, request_id="wd-smoke-req-001",
                                           coins=10000, phone="0912345678", method="telebirr",
                                           withdraw_id=90001))
            db.session.commit()
            try:
                db.session.add(WithdrawRequest(sender_id=7101, request_id="wd-smoke-req-001",
                                               coins=10000, phone="0912345678", method="telebirr",
                                               withdraw_id=90002))
                with self.assertRaises(IntegrityError):
                    db.session.commit()
            finally:
                db.session.rollback()
                WithdrawRequest.query.filter_by(sender_id=7101, request_id="wd-smoke-req-001").delete()
                db.session.commit()

    def test_pk_gift_request_table_and_unique_idempotency_constraint(self):
        from backend.main import PKGiftRequest
        from sqlalchemy.exc import IntegrityError
        with app.app_context():
            from sqlalchemy import inspect
            self.assertTrue(inspect(db.engine).has_table('pk_gift_request'))
            db.session.add(PKGiftRequest(sender_id=7001, request_id="pk-smoke-req-001",
                                         pk_id=81, to_host=1, gift_key="rose",
                                         qty=2, total=20, real_used=20))
            db.session.commit()
            try:
                db.session.add(PKGiftRequest(sender_id=7001, request_id="pk-smoke-req-001",
                                             pk_id=81, to_host=1, gift_key="rose",
                                             qty=2, total=20, real_used=20))
                with self.assertRaises(IntegrityError):
                    db.session.commit()
            finally:
                db.session.rollback()
                PKGiftRequest.query.filter_by(sender_id=7001, request_id="pk-smoke-req-001").delete()
                db.session.commit()

    def test_purchase_reference_indexes_are_case_insensitive_and_unique(self):
        from sqlalchemy import inspect, func
        from sqlalchemy.exc import IntegrityError
        from backend.main import Purchase
        with app.app_context():
            from sqlalchemy import text
            indexes = set(db.session.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='purchase'"
            )).scalars().all())
            self.assertIn("uq_purchase_ref_ci", indexes)
            self.assertIn("uq_purchase_txhash_ci", indexes)
            db.session.add(Purchase(user_id=8101, username="smoke", coins=100, birr=50,
                                    ref="TEL-REF-001", method="telebirr", txhash=""))
            db.session.commit()
            try:
                db.session.add(Purchase(user_id=8102, username="smoke2", coins=100,
                                        birr=50, ref="tel-ref-001", method="telebirr", txhash=""))
                with self.assertRaises(IntegrityError):
                    db.session.commit()
            finally:
                db.session.rollback()
                Purchase.query.filter(func.lower(Purchase.ref) == "tel-ref-001").delete(synchronize_session=False)
                db.session.commit()

    def test_financial_reconciliation_runs_on_empty_database(self):
        with app.app_context():
            result = FIN["reconcile"]()
            self.assertTrue(result["ok"], result["issues"])
            self.assertEqual(result["eligible_gift_base_coins"], 0)
            self.assertEqual(result["commission_paid_coins"], 0)
            self.assertEqual(result["commission_cap_coins"], 0)

    def test_new_private_endpoints_require_authentication(self):
        client = app.test_client()
        for path in ("/api/notifications", "/api/rooms/test-room/audience", "/api/pk/history", "/api/blocks", "/api/admin/reports", "/api/creator/dashboard"):

            response = client.get(path)
            self.assertEqual(response.status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
