"""Payment-control and accounting tests for seller wholesale orders."""
import os
import tempfile
import uuid

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-seller-orders-")
os.environ.setdefault("DATA_DIR", _TEST_DATA)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TEST_DATA, "seller-orders.db"))
os.environ.setdefault("SECRET_KEY", "seller-orders-test-secret")

import pytest
from backend import main as m


@pytest.fixture
def seller_order_api():
    suffix = uuid.uuid4().hex[:12]
    with m.app.app_context():
        m.db.create_all()
        admin = m.User(tg_id="6746329545", username="seller-review-admin", coins=0, real_coins=0)
        seller_user = m.User(tg_id="seller-order-" + suffix, username="wholesale-seller",
                             coins=0, real_coins=0, banned=False)
        m.db.session.add_all([admin, seller_user])
        m.db.session.flush()
        seller = m.SELLER["Seller"](user_id=seller_user.id, status="approved", tier="starter")
        m.db.session.add(seller)
        m.db.session.commit()
        client = m.app.test_client()
        headers = {"Authorization": "Bearer " + m.sign(admin.id)}
        yield client, headers, admin, seller_user
        m.db.session.rollback()
        orders = m.SELLER["SellerOrder"].query.filter_by(seller_id=seller_user.id).all()
        order_ids = [o.id for o in orders]
        m.db.session.query(m.SELLER["SellerOrderReview"]).filter(
            m.SELLER["SellerOrderReview"].order_id.in_(order_ids or [-1])
        ).delete(synchronize_session=False)
        m.db.session.query(m.SELLER["SellerShot"]).filter(
            m.SELLER["SellerShot"].order_id.in_(order_ids or [-1])
        ).delete(synchronize_session=False)
        entries = m.JOURNAL["JournalEntry"].query.filter(
            m.JOURNAL["JournalEntry"].source == "seller_wholesale",
            m.JOURNAL["JournalEntry"].reference.in_([str(i) for i in order_ids] or ["__none__"])
        ).all()
        entry_ids = [e.id for e in entries]
        if entry_ids:
            m.db.session.query(m.JOURNAL["JournalLine"]).filter(
                m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)
            ).delete(synchronize_session=False)
            m.db.session.query(m.JOURNAL["JournalEntry"]).filter(
                m.JOURNAL["JournalEntry"].id.in_(entry_ids)
            ).delete(synchronize_session=False)
        m.db.session.query(m.SELLER["SellerOrder"]).filter(
            m.SELLER["SellerOrder"].seller_id == seller_user.id
        ).delete(synchronize_session=False)
        m.db.session.query(m.SELLER["Seller"]).filter_by(user_id=seller_user.id).delete()
        m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([admin.id, seller_user.id])).delete(synchronize_session=False)
        m.db.session.query(m.User).filter(m.User.id.in_([admin.id, seller_user.id])).delete(synchronize_session=False)
        m.db.session.commit()


def _pending_order(seller, *, method="telebirr", ref="TB-12345678", screenshot=True):
    order = m.SELLER["SellerOrder"](
        seller_id=seller.id, birr=5000, price=0.42, coins=10000, bonus=500,
        tier="starter", ref=ref, method=method, status="pending"
    )
    m.db.session.add(order)
    m.db.session.flush()
    if screenshot:
        m.db.session.add(m.SELLER["SellerShot"](
            order_id=order.id, data=b"\xff\xd8\xfftest-image", mime="image/jpeg"
        ))
    m.db.session.commit()
    return order


def test_seller_telebirr_approval_requires_manual_verification(seller_order_api):
    client, headers, _, seller = seller_order_api
    order = _pending_order(seller)
    response = client.post("/api/seller/admin/order", json={"id": order.id, "action": "approve"}, headers=headers)
    assert response.status_code == 400
    m.db.session.expire_all()
    assert m.db.session.get(m.SELLER["SellerOrder"], order.id).status == "pending"
    assert m.db.session.get(m.User, seller.id).coins == 0
    assert m.SELLER["SellerOrderReview"].query.filter_by(order_id=order.id).count() == 0


def test_seller_telebirr_approval_posts_review_and_balanced_journal(seller_order_api):
    client, headers, _, seller = seller_order_api
    order = _pending_order(seller)
    response = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "approve", "manual_verified": True
    }, headers=headers)
    assert response.status_code == 200
    m.db.session.expire_all()
    assert m.db.session.get(m.SELLER["SellerOrder"], order.id).status == "approved"
    credited = m.db.session.get(m.User, seller.id)
    assert credited.coins == 10500
    assert credited.real_coins == 10000
    review = m.SELLER["SellerOrderReview"].query.filter_by(order_id=order.id).one()
    assert review.action == "approve"
    entries = m.JOURNAL["JournalEntry"].query.filter_by(
        source="seller_wholesale", reference=str(order.id)
    ).all()
    assert {e.currency for e in entries} == {"ETB", "COIN"}
    for entry in entries:
        lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
        assert sum(x.debit for x in lines) == sum(x.credit for x in lines)


def test_seller_rejection_requires_reason_and_records_audit(seller_order_api):
    client, headers, _, seller = seller_order_api
    order = _pending_order(seller)
    missing = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "reject"
    }, headers=headers)
    assert missing.status_code == 400
    rejected = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "reject", "reason": "Payment reference does not match"
    }, headers=headers)
    assert rejected.status_code == 200
    review = m.SELLER["SellerOrderReview"].query.filter_by(order_id=order.id).one()
    assert review.action == "reject"
    assert review.note == "Payment reference does not match"
    assert m.db.session.get(m.User, seller.id).coins == 0


def test_seller_usdt_order_requires_valid_full_length_hash(seller_order_api):
    client, headers, _, seller = seller_order_api
    order = _pending_order(seller, method="usdt", ref="ABC123")
    response = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "approve"
    }, headers=headers)
    assert response.status_code == 422
    assert m.db.session.get(m.SELLER["SellerOrder"], order.id).status == "pending"


def test_seller_order_review_requires_screenshot_and_valid_action(seller_order_api):
    client, headers, _, seller = seller_order_api
    order = _pending_order(seller, screenshot=False)
    no_shot = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "reject", "reason": "No valid receipt"
    }, headers=headers)
    assert no_shot.status_code == 422
    bad_action = client.post("/api/seller/admin/order", json={
        "id": order.id, "action": "delete"
    }, headers=headers)
    assert bad_action.status_code == 400
