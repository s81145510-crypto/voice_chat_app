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
