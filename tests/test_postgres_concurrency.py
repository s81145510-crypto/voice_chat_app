"""PostgreSQL-only integration tests for concurrent financial requests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from backend import main as m


def test_two_simultaneous_withdrawals_cannot_overdraw_earnings(monkeypatch):
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level locking")
    monkeypatch.setattr(m, "notify_admins", lambda *args, **kwargs: None)

    marker = uuid4().hex[:16]
    tg_id = "pg-race-" + marker
    with m.app.app_context():
        user = m.User(
            tg_id=tg_id, username="concurrency-test",
            coins=0, real_coins=0, earnings=700, commission=0,
            phone="0912345678",
        )
        m.db.session.add(user)
        m.db.session.commit()
        user_id = user.id
        token = m.sign(user_id)

    barrier = Barrier(2)

    def submit_withdrawal():
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post(
            "/api/withdraw",
            json={"coins": 500, "method": "telebirr", "phone": "0912345678"},
            headers={"Authorization": "Bearer " + token},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit_withdrawal) for _ in range(2)]
            responses = [future.result(timeout=30) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409]

        with m.app.app_context():
            saved_user = m.db.session.get(m.User, user_id)
            assert saved_user.earnings == 200
            assert saved_user.commission == 0
            withdrawals = m.Withdraw.query.filter_by(user_id=user_id).all()
            assert len(withdrawals) == 1
            withdrawal = withdrawals[0]
            assert withdrawal.coins == 500
            assert withdrawal.earnings_coins == 500
            assert withdrawal.commission_coins == 0

            entry = m.JOURNAL["JournalEntry"].query.filter_by(
                source="withdrawal_reserve", reference=str(withdrawal.id)
            ).one()
            lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
            assert sum(int(line.debit) for line in lines) == 500
            assert sum(int(line.credit) for line in lines) == 500
    finally:
        with m.app.app_context():
            withdrawal_ids = [
                row.id for row in m.Withdraw.query.filter_by(user_id=user_id).all()
            ]
            references = [str(value) for value in withdrawal_ids]
            entries = m.JOURNAL["JournalEntry"].query.filter(
                m.JOURNAL["JournalEntry"].source == "withdrawal_reserve",
                m.JOURNAL["JournalEntry"].reference.in_(references or [""]),
            ).all()
            entry_ids = [entry.id for entry in entries]
            if entry_ids:
                m.JOURNAL["JournalLine"].query.filter(
                    m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)
                ).delete(synchronize_session=False)
                m.JOURNAL["JournalEntry"].query.filter(
                    m.JOURNAL["JournalEntry"].id.in_(entry_ids)
                ).delete(synchronize_session=False)
            m.Withdraw.query.filter_by(user_id=user_id).delete(synchronize_session=False)
            m.Tx.query.filter_by(user_id=user_id).delete(synchronize_session=False)
            m.db.session.query(m.User).filter_by(id=user_id).delete(synchronize_session=False)
            m.db.session.commit()


def test_two_hosts_cannot_claim_the_same_pk_slot(monkeypatch):
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level locking")
    marker = uuid4().hex[:16]
    with m.app.app_context():
        owner = m.User(tg_id="pk-owner-" + marker, username="pk-owner", host_status="host")
        host_a = m.User(tg_id="pk-a-" + marker, username="pk-a", host_status="host")
        host_b = m.User(tg_id="pk-b-" + marker, username="pk-b", host_status="host")
        m.db.session.add_all([owner, host_a, host_b])
        m.db.session.flush()
        pk = m.PKBattle(host1_id=owner.id, status="active", host2_id=None, duration=300)
        m.db.session.add(pk)
        m.db.session.commit()
        pk_id = pk.id
        users = [(host_a.id, m.sign(host_a.id)), (host_b.id, m.sign(host_b.id))]
        user_ids = [owner.id, host_a.id, host_b.id]

    barrier = Barrier(2)

    def join_pk(token):
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post(
            "/api/pk/join",
            json={"pk_id": pk_id},
            headers={"Authorization": "Bearer " + token},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(join_pk, token) for _, token in users]
            responses = [future.result(timeout=30) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 409]
        with m.app.app_context():
            saved_pk = m.db.session.get(m.PKBattle, pk_id)
            assert saved_pk.host2_id in {users[0][0], users[1][0]}
            assert saved_pk.status == "active"
    finally:
        with m.app.app_context():
            m.db.session.query(m.PKBattle).filter_by(id=pk_id).delete()
            m.db.session.query(m.User).filter(m.User.id.in_(user_ids)).delete(synchronize_session=False)
            m.db.session.commit()



def test_concurrent_lucky_stats_updates_are_not_lost():
    """Two simultaneous Lucky gifts must both contribute to the daily totals."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL upsert/concurrency semantics")
        day = m.today()
        m.db.session.query(m.LuckyStats).filter_by(day=day).delete()
        m.db.session.commit()

    barrier = Barrier(2)

    def record_gift():
        with m.app.app_context():
            barrier.wait(timeout=10)
            m.record_lucky_stats(10, 0)
            m.db.session.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(record_gift) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)

        with m.app.app_context():
            stats = m.db.session.get(m.LuckyStats, m.today())
            assert stats is not None
            assert stats.volume == 20
            assert stats.returned == 0
            assert stats.house == 20
    finally:
        with m.app.app_context():
            m.db.session.query(m.LuckyStats).filter_by(day=m.today()).delete()
            m.db.session.commit()



def test_two_simultaneous_purchase_approvals_credit_only_once(monkeypatch):
    """Two concurrent admins cannot approve and credit the same pending purchase twice."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level update concurrency")
    monkeypatch.setitem(m.FIN, "ledger", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (None, False))

    marker = uuid4().hex[:16]
    admin_tg = "6746329545"
    customer_tg = "purchase-race-" + marker
    with m.app.app_context():
        admin = m.User(tg_id=admin_tg, username="purchase-race-admin", coins=0, real_coins=0)
        customer = m.User(tg_id=customer_tg, username="purchase-race-customer", coins=0, real_coins=0)
        m.db.session.add_all([admin, customer])
        m.db.session.flush()
        purchase = m.Purchase(
            user_id=customer.id, username=customer.username, coins=100, birr=50,
            ref="TB-" + marker.upper(), status="pending", method="telebirr", txhash=""
        )
        m.db.session.add(purchase)
        m.db.session.flush()
        m.db.session.add(m.Shot(purchase_id=purchase.id, data=b"\\xff\\xd8\\xfftest", mime="image/jpeg"))
        m.db.session.commit()
        purchase_id, customer_id, admin_id = purchase.id, customer.id, admin.id
        token = m.sign(admin.id)

    barrier = Barrier(2)

    def approve():
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post(
            "/api/admin/purchase",
            json={"id": purchase_id, "action": "approve", "manual_verified": True},
            headers={"Authorization": "Bearer " + token},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve) for _ in range(2)]
            responses = [future.result(timeout=30) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409]
        with m.app.app_context():
            saved_customer = m.db.session.get(m.User, customer_id)
            saved_purchase = m.db.session.get(m.Purchase, purchase_id)
            assert saved_purchase.status == "approved"
            assert saved_customer.coins == 100
            assert saved_customer.real_coins == 100
            assert m.PurchaseReview.query.filter_by(purchase_id=purchase_id).count() == 1
            assert m.Tx.query.filter_by(user_id=customer_id, kind="buy").count() == 1
    finally:
        with m.app.app_context():
            m.db.session.query(m.PurchaseReview).filter_by(purchase_id=purchase_id).delete()
            m.db.session.query(m.Shot).filter_by(purchase_id=purchase_id).delete()
            m.db.session.query(m.Purchase).filter_by(id=purchase_id).delete()
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([customer_id, admin_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([customer_id, admin_id])).delete(synchronize_session=False)
            m.db.session.commit()
