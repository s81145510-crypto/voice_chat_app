"""Regression tests for admin wallet grant accounting and purchased/promo split safety."""
import pytest

from backend import main as m


@pytest.fixture
def admin_grant_api():
    with m.app.app_context():
        m.db.create_all()
        admin_id, target_id = "journal-admin-grant-test", "journal-grant-target"
        old = m.User.query.filter(m.User.tg_id.in_([admin_id, target_id])).all()
        old_ids = [u.id for u in old]
        if old_ids:
            m.Tx.query.filter(m.Tx.user_id.in_(old_ids)).delete(synchronize_session=False)
            entries = m.JOURNAL["JournalEntry"].query.filter_by(created_by=admin_id).all()
            eids = [e.id for e in entries]
            if eids:
                m.JOURNAL["JournalLine"].query.filter(m.JOURNAL["JournalLine"].entry_id.in_(eids)).delete(synchronize_session=False)
                m.JOURNAL["JournalEntry"].query.filter(m.JOURNAL["JournalEntry"].id.in_(eids)).delete(synchronize_session=False)
            m.User.query.filter(m.User.id.in_(old_ids)).delete(synchronize_session=False)
            m.db.session.commit()
        admin = m.User(tg_id=admin_id, username="journal-admin", coins=0, real_coins=0)
        target = m.User(tg_id=target_id, username="grant-target", coins=100, real_coins=80)
        m.db.session.add_all([admin, target])
        m.db.session.commit()
        m.ADMIN_IDS.add(admin_id)
        client = m.app.test_client()
        headers = {"Authorization": "Bearer " + m.sign(admin.id)}
        yield client, admin, target, headers
        m.db.session.rollback()
        ids = [admin.id, target.id]
        m.Tx.query.filter(m.Tx.user_id.in_(ids)).delete(synchronize_session=False)
        entries = m.JOURNAL["JournalEntry"].query.filter_by(created_by=admin_id).all()
        eids = [e.id for e in entries]
        if eids:
            m.JOURNAL["JournalLine"].query.filter(m.JOURNAL["JournalLine"].entry_id.in_(eids)).delete(synchronize_session=False)
            m.JOURNAL["JournalEntry"].query.filter(m.JOURNAL["JournalEntry"].id.in_(eids)).delete(synchronize_session=False)
        m.User.query.filter(m.User.id.in_(ids)).delete(synchronize_session=False)
        m.db.session.commit()
        m.ADMIN_IDS.discard(admin_id)


def _grant_entry(admin_id, amount):
    tx = m.Tx.query.filter_by(kind="grant", amount=amount).order_by(m.Tx.id.desc()).first()
    assert tx is not None
    entry = m.JOURNAL["JournalEntry"].query.filter_by(idempotency_key=f"admin-grant:{tx.id}:COIN").one()
    lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines)
    return {x.account_code: x for x in lines}


def test_positive_admin_grant_is_promo_and_journaled(admin_grant_api):
    client, admin, target, headers = admin_grant_api
    response = client.post("/api/admin/user", json={"id": target.id, "action": "grant", "amount": 25}, headers=headers)
    assert response.status_code == 200
    m.db.session.refresh(target)
    assert target.coins == 125 and target.real_coins == 80
    lines = _grant_entry(admin.id, 25)
    assert lines["expense.coin_issuance"].debit == 25
    assert lines["liability.promo_coin_balance"].credit == 25


def test_negative_admin_grant_removes_promo_before_purchased_and_requires_reason(admin_grant_api):
    client, admin, target, headers = admin_grant_api
    missing = client.post("/api/admin/user", json={"id": target.id, "action": "grant", "amount": -30}, headers=headers)
    assert missing.status_code == 400
    m.db.session.refresh(target)
    assert target.coins == 100 and target.real_coins == 80

    response = client.post("/api/admin/user", json={
        "id": target.id, "action": "grant", "amount": -30, "reason": "duplicate credit correction"
    }, headers=headers)
    assert response.status_code == 200
    m.db.session.refresh(target)
    assert target.coins == 70 and target.real_coins == 70
    lines = _grant_entry(admin.id, -30)
    assert lines["liability.promo_coin_balance"].debit == 20
    assert lines["liability.user_coin_balance"].debit == 10
    assert lines["equity.admin_wallet_adjustment"].credit == 30


def test_admin_grant_journal_failure_rolls_back_wallet(monkeypatch, admin_grant_api):
    client, admin, target, headers = admin_grant_api
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("journal down")))
    response = client.post("/api/admin/user", json={"id": target.id, "action": "grant", "amount": 25}, headers=headers)
    assert response.status_code == 500
    m.db.session.refresh(target)
    assert target.coins == 100 and target.real_coins == 80
    assert m.Tx.query.filter_by(user_id=target.id, kind="grant").count() == 0
