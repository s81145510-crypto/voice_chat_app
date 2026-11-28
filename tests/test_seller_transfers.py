"""Integration tests for retry-safe seller coin transfers."""
import os
import tempfile
import uuid

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-seller-transfer-")
os.environ.setdefault("DATA_DIR", _TEST_DATA)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TEST_DATA, "seller-transfer.db"))
os.environ.setdefault("SECRET_KEY", "seller-transfer-test-secret")

import pytest
from backend import main as m


@pytest.fixture
def seller_api():
    suffix = uuid.uuid4().hex[:12]
    with m.app.app_context():
        m.db.create_all()
        seller_user = m.User(
            tg_id="seller-" + suffix, username="seller-test",
            coins=1000, real_coins=1000, banned=False
        )
        buyer = m.User(
            tg_id="buyer-" + suffix, username="buyer-test",
            coins=0, real_coins=0, banned=False
        )
        m.db.session.add_all([seller_user, buyer])
        m.db.session.flush()
        seller = m.SELLER["Seller"](user_id=seller_user.id, status="approved", tier="starter")
        m.db.session.add(seller)
        m.db.session.commit()
        client = m.app.test_client()
        headers = {"Authorization": "Bearer " + m.sign(seller_user.id)}
        yield client, headers, seller_user, buyer
        m.db.session.rollback()
        m.db.session.query(m.SELLER["SellerTransfer"]).filter(
            m.SELLER["SellerTransfer"].from_id == seller_user.id
        ).delete(synchronize_session=False)
        m.db.session.query(m.SELLER["Seller"]).filter_by(user_id=seller_user.id).delete()
        m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([seller_user.id, buyer.id])).delete(synchronize_session=False)
        m.db.session.query(m.JOURNAL["JournalLine"]).delete(synchronize_session=False)
        m.db.session.query(m.JOURNAL["JournalEntry"]).delete(synchronize_session=False)
        m.db.session.query(m.User).filter(m.User.id.in_([seller_user.id, buyer.id])).delete(synchronize_session=False)
        m.db.session.commit()


def test_seller_transfer_exact_retry_does_not_debit_twice(seller_api):
    client, headers, seller, buyer = seller_api
    payload = {"to": buyer.id, "amount": 100, "request_id": "seller-transfer-retry-0001"}

    first = client.post("/api/seller/transfer", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json["received"] == 98
    assert first.json["replayed"] is False

    second = client.post("/api/seller/transfer", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json["replayed"] is True
    assert second.json["transfer_id"] == first.json["transfer_id"]

    m.db.session.expire_all()
    assert m.db.session.get(m.User, seller.id).coins == 900
    assert m.db.session.get(m.User, seller.id).real_coins == 900
    assert m.db.session.get(m.User, buyer.id).coins == 98
    assert m.db.session.get(m.User, buyer.id).real_coins == 98
    assert m.SELLER["SellerTransfer"].query.filter_by(request_id=payload["request_id"]).count() == 1


def test_seller_transfer_request_id_cannot_be_reused_for_different_payload(seller_api):
    client, headers, _, buyer = seller_api
    payload = {"to": buyer.id, "amount": 100, "request_id": "seller-transfer-retry-0002"}
    assert client.post("/api/seller/transfer", json=payload, headers=headers).status_code == 200

    changed = dict(payload, amount=200)
    response = client.post("/api/seller/transfer", json=changed, headers=headers)
    assert response.status_code == 409


def test_seller_transfer_requires_request_id(seller_api):
    client, headers, _, buyer = seller_api
    response = client.post(
        "/api/seller/transfer", json={"to": buyer.id, "amount": 100}, headers=headers
    )
    assert response.status_code == 400
