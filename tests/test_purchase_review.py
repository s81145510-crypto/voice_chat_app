"""Persistence tests for the auditable admin purchase-review record."""
import os
import tempfile

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-purchase-review-")
os.environ.setdefault("DATA_DIR", _TEST_DATA)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TEST_DATA, "review.db"))
os.environ.setdefault("SECRET_KEY", "purchase-review-test-secret")

import pytest
from sqlalchemy.exc import IntegrityError
from backend.main import app, db, Purchase, PurchaseReview


@pytest.fixture
def session():
    with app.app_context():
        db.create_all()
        db.session.query(PurchaseReview).delete()
        db.session.query(Purchase).delete()
        db.session.commit()
        yield db.session
        db.session.rollback()
        db.session.query(PurchaseReview).delete()
        db.session.query(Purchase).delete()
        db.session.commit()


def test_review_record_keeps_action_reviewer_reference_and_reason(session):
    purchase = Purchase(
        user_id=101, username="test-user", coins=100, birr=50,
        ref="TB-12345678", status="rejected", method="telebirr", txhash=""
    )
    session.add(purchase)
    session.flush()
    session.add(PurchaseReview(
        purchase_id=purchase.id, action="reject", reviewer_tg_id="9001",
        reviewed_at=1760000000, reference_snapshot=purchase.ref,
        note="Receipt did not match the transfer"
    ))
    session.commit()

    saved = session.query(PurchaseReview).filter_by(purchase_id=purchase.id).one()
    assert saved.action == "reject"
    assert saved.reviewer_tg_id == "9001"
    assert saved.reference_snapshot == "TB-12345678"
    assert saved.note == "Receipt did not match the transfer"


def test_only_one_review_record_can_exist_per_purchase(session):
    purchase = Purchase(
        user_id=102, username="test-user", coins=100, birr=50,
        ref="TB-87654321", status="approved", method="telebirr", txhash=""
    )
    session.add(purchase)
    session.flush()
    session.add(PurchaseReview(
        purchase_id=purchase.id, action="approve", reviewer_tg_id="9001",
        reference_snapshot=purchase.ref, note=""
    ))
    session.commit()

    session.add(PurchaseReview(
        purchase_id=purchase.id, action="approve", reviewer_tg_id="9002",
        reference_snapshot=purchase.ref, note=""
    ))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.query(PurchaseReview).filter_by(purchase_id=purchase.id).count() == 1


@pytest.fixture
def purchase_api(monkeypatch):
    """Isolated Flask client and users for admin purchase-route tests."""
    from backend import main as m

    with m.app.app_context():
        m.db.create_all()
        m.db.session.query(m.PurchaseReview).delete()
        m.db.session.query(m.Shot).delete()
        m.db.session.query(m.Purchase).delete()
        m.db.session.query(m.Tx).delete()
        m.db.session.query(m.User).filter(m.User.tg_id.in_(["6746329545", "review-customer", "review-nonadmin"])).delete(synchronize_session=False)
        admin = m.User(tg_id="6746329545", username="review-admin", coins=0, real_coins=0)
        customer = m.User(tg_id="review-customer", username="review-customer", coins=0, real_coins=0)
        nonadmin = m.User(tg_id="review-nonadmin", username="review-nonadmin", coins=0, real_coins=0)
        m.db.session.add_all([admin, customer, nonadmin])
        m.db.session.commit()
        client = m.app.test_client()
        headers = {"Authorization": "Bearer " + m.sign(admin.id)}
        nonadmin_headers = {"Authorization": "Bearer " + m.sign(nonadmin.id)}
        yield m, client, admin, customer, nonadmin, headers, nonadmin_headers
        m.db.session.rollback()
        m.db.session.query(m.PurchaseReview).delete()
        m.db.session.query(m.Shot).delete()
        m.db.session.query(m.Purchase).delete()
        m.db.session.query(m.Tx).delete()
        m.db.session.query(m.User).filter(m.User.id.in_([admin.id, customer.id, nonadmin.id])).delete(synchronize_session=False)
        m.db.session.commit()


def _new_purchase(m, customer, *, method="telebirr", ref="TB-12345678", txhash="", screenshot=True):
    purchase = m.Purchase(
        user_id=customer.id, username=customer.username, coins=100, birr=50,
        ref=ref, status="pending", method=method, txhash=txhash
    )
    m.db.session.add(purchase)
    m.db.session.flush()
    if screenshot:
        m.db.session.add(m.Shot(
            purchase_id=purchase.id, data=b"\\xff\\xd8\\xfftest-image", mime="image/jpeg"
        ))
    m.db.session.commit()
    return purchase


def test_admin_purchase_approval_requires_receipt_and_manual_flag(purchase_api, monkeypatch):
    m, client, admin, customer, _, headers, _ = purchase_api
    purchase = _new_purchase(m, customer, screenshot=False)

    response = client.post("/api/admin/purchase", json={"id": purchase.id, "action": "approve", "manual_verified": True}, headers=headers)
    assert response.status_code == 422
    assert m.db.session.get(m.Purchase, purchase.id).status == "pending"
    assert m.db.session.get(m.User, customer.id).coins == 0
    assert m.PurchaseReview.query.filter_by(purchase_id=purchase.id).count() == 0

    m.db.session.add(m.Shot(purchase_id=purchase.id, data=b"\\xff\\xd8\\xfftest-image", mime="image/jpeg"))
    m.db.session.commit()
    response = client.post("/api/admin/purchase", json={"id": purchase.id, "action": "approve"}, headers=headers)
    assert response.status_code == 400
    assert m.db.session.get(m.Purchase, purchase.id).status == "pending"
    assert m.db.session.get(m.User, customer.id).coins == 0
    assert m.PurchaseReview.query.filter_by(purchase_id=purchase.id).count() == 0


def test_admin_telebirr_approval_records_audit_and_credits_once(purchase_api, monkeypatch):
    m, client, admin, customer, _, headers, _ = purchase_api
    purchase = _new_purchase(m, customer)
    monkeypatch.setitem(m.FIN, "ledger", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (None, False))

    response = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "approve", "manual_verified": True
    }, headers=headers)
    assert response.status_code == 200
    assert response.json["status"] == "approved"
    assert response.json["reviewed_by"] == "6746329545"
    m.db.session.expire_all()
    assert m.db.session.get(m.Purchase, purchase.id).status == "approved"
    saved_customer = m.db.session.get(m.User, customer.id)
    assert saved_customer.coins == 100
    assert saved_customer.real_coins == 100
    review = m.PurchaseReview.query.filter_by(purchase_id=purchase.id).one()
    assert review.action == "approve"
    assert review.reviewer_tg_id == "6746329545"
    assert review.reference_snapshot == "TB-12345678"

    second = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "approve", "manual_verified": True
    }, headers=headers)
    assert second.status_code == 409
    m.db.session.expire_all()
    assert m.db.session.get(m.User, customer.id).coins == 100
    assert m.PurchaseReview.query.filter_by(purchase_id=purchase.id).count() == 1


def test_purchase_approval_journals_referral_and_invite_as_promo_coins(purchase_api, monkeypatch):
    m, client, admin, customer, _, headers, _ = purchase_api
    agent = m.User(tg_id="review-agent", username="review-agent", coins=0, real_coins=0)
    m.db.session.add(agent)
    m.db.session.flush()
    customer.referrer_id = admin.id
    customer.agent_id = agent.id
    m.db.session.commit()
    purchase = _new_purchase(m, customer)
    monkeypatch.setitem(m.FIN, "ledger", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)

    try:
        response = client.post("/api/admin/purchase", json={
            "id": purchase.id, "action": "approve", "manual_verified": True
        }, headers=headers)
        assert response.status_code == 200
        entry = m.JOURNAL["JournalEntry"].query.filter_by(
            idempotency_key=f"purchase:{purchase.id}:COIN"
        ).one()
        lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
        credits = {(line.account_code, line.user_id): line.credit for line in lines if line.credit}
        assert credits[("liability.user_coin_balance", customer.id)] == 100
        assert credits[("liability.promo_coin_balance", admin.id)] == 10
        assert credits[("liability.promo_coin_balance", agent.id)] == m.INVITE_REWARD
        assert m.db.session.get(m.User, customer.id).coins == 100
        assert m.db.session.get(m.User, admin.id).coins == 10
        assert m.db.session.get(m.User, agent.id).coins == m.INVITE_REWARD
    finally:
        entry = m.JOURNAL["JournalEntry"].query.filter_by(
            idempotency_key=f"purchase:{purchase.id}:COIN"
        ).first()
        if entry:
            m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).delete()
            m.db.session.delete(entry)
        m.db.session.query(m.User).filter_by(id=agent.id).delete()
        m.db.session.commit()


def test_admin_rejection_requires_reason_and_preserves_reason_in_audit(purchase_api):
    m, client, admin, customer, _, headers, _ = purchase_api
    purchase = _new_purchase(m, customer)
    missing_reason = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "reject"
    }, headers=headers)
    assert missing_reason.status_code == 400
    assert m.db.session.get(m.Purchase, purchase.id).status == "pending"

    response = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "reject", "reason": "Transfer reference does not match"
    }, headers=headers)
    assert response.status_code == 200
    m.db.session.expire_all()
    assert m.db.session.get(m.Purchase, purchase.id).status == "rejected"
    assert m.db.session.get(m.User, customer.id).coins == 0
    review = m.PurchaseReview.query.filter_by(purchase_id=purchase.id).one()
    assert review.action == "reject"
    assert review.note == "Transfer reference does not match"


def test_usdt_approval_cannot_use_manual_verification_bypass(purchase_api, monkeypatch):
    m, client, admin, customer, _, headers, _ = purchase_api
    purchase = _new_purchase(
        m, customer, method="usdt",
        ref="INTERNAL-USDT-REF",
        txhash="a" * 64
    )
    def reject_chain_check(**kwargs):
        raise m.TronVerificationError("TRON payment not verified")
    monkeypatch.setattr(m, "verify_usdt_trc20_payment", reject_chain_check)

    response = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "approve", "manual_verified": True
    }, headers=headers)
    assert response.status_code == 422
    m.db.session.expire_all()
    assert m.db.session.get(m.Purchase, purchase.id).status == "pending"
    assert m.db.session.get(m.User, customer.id).coins == 0
    assert m.PurchaseReview.query.filter_by(purchase_id=purchase.id).count() == 0


def test_receipt_image_endpoint_is_admin_only(purchase_api):
    m, client, admin, customer, nonadmin, headers, nonadmin_headers = purchase_api
    purchase = _new_purchase(m, customer)
    forbidden = client.get(f"/api/admin/shot/{purchase.id}", headers=nonadmin_headers)
    assert forbidden.status_code == 403
    allowed = client.get(f"/api/admin/shot/{purchase.id}", headers=headers)
    assert allowed.status_code == 200
    assert allowed.data.startswith(b"\\xff\\xd8\\xff")


def test_failed_journal_rolls_back_purchase_and_sends_no_referral_notice(purchase_api, monkeypatch):
    m, client, admin, customer, _, headers, _ = purchase_api
    customer.referrer_id = admin.id
    m.db.session.commit()
    purchase = _new_purchase(m, customer)
    monkeypatch.setitem(m.FIN, "ledger", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)
    sent = []
    monkeypatch.setattr(m, "notify", lambda *args, **kwargs: sent.append(args))
    def fail_journal(**kwargs):
        raise RuntimeError("simulated journal failure")
    monkeypatch.setitem(m.JOURNAL, "post_internal", fail_journal)

    response = client.post("/api/admin/purchase", json={
        "id": purchase.id, "action": "approve", "manual_verified": True
    }, headers=headers)
    assert response.status_code == 500
    m.db.session.expire_all()
    assert m.db.session.get(m.Purchase, purchase.id).status == "pending"
    assert m.db.session.get(m.User, customer.id).coins == 0
    assert m.db.session.get(m.User, admin.id).coins == 0
    assert m.PurchaseReview.query.filter_by(purchase_id=purchase.id).count() == 0
    assert sent == []
