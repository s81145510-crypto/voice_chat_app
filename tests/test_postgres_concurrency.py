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



def test_parallel_approvals_for_different_purchases_award_invite_once(monkeypatch):
    """Two purchases for one customer must not trigger the first-purchase reward twice."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level locking")
    monkeypatch.setitem(m.FIN, "ledger", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (None, False))
    marker = uuid4().hex[:10]
    with m.app.app_context():
        admin = m.User(tg_id="6746329545", username="invite-race-admin", coins=0, real_coins=0)
        agent = m.User(tg_id="invite-agent-" + marker, username="invite-agent", coins=0, real_coins=0)
        customer = m.User(tg_id="invite-customer-" + marker, username="invite-customer",
                          coins=0, real_coins=0)
        m.db.session.add_all([admin, agent, customer])
        m.db.session.flush()
        customer.agent_id = agent.id
        purchases = []
        for suffix in ("A", "B"):
            purchase = m.Purchase(
                user_id=customer.id, username=customer.username, coins=100, birr=50,
                ref="TB-" + marker.upper() + suffix, status="pending", method="telebirr", txhash=""
            )
            m.db.session.add(purchase)
            m.db.session.flush()
            m.db.session.add(m.Shot(purchase_id=purchase.id, data=b"\\xff\\xd8\\xfftest", mime="image/jpeg"))
            purchases.append(purchase.id)
        m.db.session.commit()
        admin_id, agent_id, customer_id = admin.id, agent.id, customer.id
        token = m.sign(admin_id)

    barrier = Barrier(2)

    def approve(purchase_id):
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post("/api/admin/purchase",
                           json={"id": purchase_id, "action": "approve", "manual_verified": True},
                           headers={"Authorization": "Bearer " + token})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve, pid) for pid in purchases]
            responses = [future.result(timeout=30) for future in futures]
        assert [response.status_code for response in responses] == [200, 200]
        with m.app.app_context():
            saved_customer = m.db.session.get(m.User, customer_id)
            saved_agent = m.db.session.get(m.User, agent_id)
            assert saved_customer.coins == 200
            assert saved_agent.coins == m.INVITE_REWARD
            assert m.Tx.query.filter_by(user_id=customer_id, kind="invite_reward").count() == 1
            assert m.Tx.query.filter_by(user_id=agent_id, kind="invite").count() == 1
            assert m.PurchaseReview.query.filter(m.PurchaseReview.purchase_id.in_(purchases)).count() == 2
    finally:
        with m.app.app_context():
            m.db.session.query(m.PurchaseReview).filter(m.PurchaseReview.purchase_id.in_(purchases)).delete(synchronize_session=False)
            m.db.session.query(m.Shot).filter(m.Shot.purchase_id.in_(purchases)).delete(synchronize_session=False)
            m.db.session.query(m.Purchase).filter(m.Purchase.id.in_(purchases)).delete(synchronize_session=False)
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([admin_id, agent_id, customer_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([admin_id, agent_id, customer_id])).delete(synchronize_session=False)
            m.db.session.commit()


def test_concurrent_room_gifts_cannot_overspend_purchased_coins(monkeypatch):
    """Two simultaneous gifts share one sender balance; only affordable spend commits."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level locking")
    monkeypatch.setattr(m, "limited", lambda *args, **kwargs: False)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)

    marker = uuid4().hex[:10]
    with m.app.app_context():
        sender = m.User(tg_id="gift-sender-" + marker, username="gift-sender",
                        coins=100, real_coins=100)
        host = m.User(tg_id="gift-host-" + marker, username="gift-host",
                      coins=0, real_coins=0, host_status="host")
        m.db.session.add_all([sender, host])
        m.db.session.flush()
        room_id = "g" + marker
        room = m.Room(id=room_id, name="Gift race", host_id=host.id)
        m.db.session.add(room)
        m.db.session.commit()
        sender_id, host_id = sender.id, host.id
        token = m.sign(sender_id)

    barrier = Barrier(2)

    def send_gift():
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post(
            f"/api/rooms/{room_id}/gift",
            json={"gift": "rose", "qty": 6, "to": host_id},
            headers={"Authorization": "Bearer " + token},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send_gift) for _ in range(2)]
            responses = [future.result(timeout=30) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 402]
        with m.app.app_context():
            saved_sender = m.db.session.get(m.User, sender_id)
            saved_host = m.db.session.get(m.User, host_id)
            assert saved_sender.coins == 40
            assert saved_sender.real_coins == 40
            assert saved_host.earnings == 30
            assert m.Tx.query.filter_by(user_id=sender_id, kind="gift").count() == 1
            gift_entries = m.JOURNAL["JournalEntry"].query.filter_by(source="gift").all()
            assert len([entry for entry in gift_entries if entry.description.startswith("gift rose")]) >= 1
    finally:
        with m.app.app_context():
            gift_entries = (m.JOURNAL["JournalEntry"].query
                            .join(m.JOURNAL["JournalLine"], m.JOURNAL["JournalLine"].entry_id == m.JOURNAL["JournalEntry"].id)
                            .filter(m.JOURNAL["JournalEntry"].source == "gift",
                                    m.JOURNAL["JournalLine"].user_id == sender_id).distinct().all())
            entry_ids = [x.id for x in gift_entries]
            if entry_ids:
                m.db.session.query(m.JOURNAL["JournalLine"]).filter(
                    m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)).delete(synchronize_session=False)
                m.db.session.query(m.JOURNAL["JournalEntry"]).filter(
                    m.JOURNAL["JournalEntry"].id.in_(entry_ids)).delete(synchronize_session=False)
            gift_model = m.db.Model.registry._class_registry.get("FinGiftTx")
            ledger_model = m.db.Model.registry._class_registry.get("FinLedger")
            if gift_model:
                gift_ids = [x.id for x in gift_model.query.filter_by(sender_id=sender_id).all()]
                gift_model.query.filter_by(sender_id=sender_id).delete(synchronize_session=False)
                if ledger_model and gift_ids:
                    ledger_model.query.filter(ledger_model.ref.in_([str(x) for x in gift_ids])).delete(synchronize_session=False)
            m.db.session.query(m.Message).filter_by(room_id=room_id).delete()
            m.db.session.query(m.Room).filter_by(id=room_id).delete()
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([sender_id, host_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([sender_id, host_id])).delete(synchronize_session=False)
            m.db.session.commit()


def test_lucky_room_gift_tracks_purchased_spend_and_promo_payout(monkeypatch):
    """Lucky payouts increase promotional balance, while the original spend uses its real source."""
    monkeypatch.setattr(m, "limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(m, "pick_weighted", lambda table: 2)
    monkeypatch.setitem(m.FIN, "wallet_sync", lambda *args, **kwargs: None)
    marker = uuid4().hex[:10]
    with m.app.app_context():
        sender = m.User(tg_id="lucky-sender-" + marker, username="lucky-sender",
                        coins=10, real_coins=10)
        host = m.User(tg_id="lucky-host-" + marker, username="lucky-host",
                      coins=0, real_coins=0, host_status="host")
        m.db.session.add_all([sender, host])
        m.db.session.flush()
        room_id = "l" + marker
        m.db.session.add(m.Room(id=room_id, name="Lucky audit", host_id=host.id))
        m.db.session.commit()
        sender_id, host_id = sender.id, host.id
        token = m.sign(sender_id)

    try:
        response = m.app.test_client().post(
            f"/api/rooms/{room_id}/gift",
            json={"gift": "lucky1", "qty": 1, "to": host_id},
            headers={"Authorization": "Bearer " + token},
        )
        assert response.status_code == 200
        with m.app.app_context():
            saved = m.db.session.get(m.User, sender_id)
            assert saved.coins == 20
            assert saved.real_coins == 0
            entry = m.JOURNAL["JournalEntry"].query.filter_by(
                source="gift", description="Lucky gift lucky1 x1"
            ).one()
            lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
            assert any(x.account_code == "liability.user_coin_balance" and
                       x.user_id == sender_id and x.debit == 10 for x in lines)
            assert any(x.account_code == "liability.promo_coin_balance" and
                       x.user_id == sender_id and x.credit == 20 for x in lines)
            stats = m.db.session.get(m.LuckyStats, m.today())
            assert stats.volume >= 10
            assert stats.returned >= 20
    finally:
        with m.app.app_context():
            entry_ids = [x.id for x in m.JOURNAL["JournalEntry"].query.filter_by(
                source="gift", description="Lucky gift lucky1 x1").all()]
            if entry_ids:
                m.db.session.query(m.JOURNAL["JournalLine"]).filter(
                    m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)).delete(synchronize_session=False)
                m.db.session.query(m.JOURNAL["JournalEntry"]).filter(
                    m.JOURNAL["JournalEntry"].id.in_(entry_ids)).delete(synchronize_session=False)
            gift_model = m.db.Model.registry._class_registry.get("FinGiftTx")
            ledger_model = m.db.Model.registry._class_registry.get("FinLedger")
            if gift_model:
                gift_ids = [x.id for x in gift_model.query.filter_by(sender_id=sender_id).all()]
                gift_model.query.filter_by(sender_id=sender_id).delete(synchronize_session=False)
                if ledger_model and gift_ids:
                    ledger_model.query.filter(ledger_model.ref.in_([str(x) for x in gift_ids])).delete(synchronize_session=False)
            m.db.session.query(m.Message).filter_by(room_id=room_id).delete()
            m.db.session.query(m.Room).filter_by(id=room_id).delete()
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([sender_id, host_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([sender_id, host_id])).delete(synchronize_session=False)
            m.db.session.query(m.LuckyStats).filter_by(day=m.today()).delete()
            m.db.session.commit()


def test_two_simultaneous_pk_finalizations_pay_hosts_only_once():
    """Concurrent end requests cannot settle one PK prize pool twice."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL conditional-update concurrency")
    marker = uuid4().hex[:10]
    with m.app.app_context():
        host1 = m.User(tg_id="pk-end-h1-" + marker, username="pk-end-host-1",
                       coins=0, real_coins=0, earnings=0, host_status="host")
        host2 = m.User(tg_id="pk-end-h2-" + marker, username="pk-end-host-2",
                       coins=0, real_coins=0, earnings=0, host_status="host")
        m.db.session.add_all([host1, host2])
        m.db.session.flush()
        pk = m.PKBattle(host1_id=host1.id, host2_id=host2.id,
                        host1_gifts=120, host2_gifts=80,
                        host1_real=120, host2_real=80,
                        status="active", started_at=m.now()-301, duration=300)
        m.db.session.add(pk)
        m.db.session.commit()
        pk_id, h1_id, h2_id = pk.id, host1.id, host2.id
        tokens = [m.sign(h1_id), m.sign(h2_id)]

    barrier = Barrier(2)

    def end(token):
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post("/api/pk/end", json={"pk_id": pk_id},
                           headers={"Authorization": "Bearer " + token})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(end, token) for token in tokens]
            responses = [future.result(timeout=30) for future in futures]
        assert [response.status_code for response in responses] == [200, 200]
        with m.app.app_context():
            saved = m.db.session.get(m.PKBattle, pk_id)
            assert saved.status == "ended"
            assert saved.winner_id == h1_id
            assert m.db.session.get(m.User, h1_id).earnings == 140
            assert m.db.session.get(m.User, h2_id).earnings == 40
            assert m.Tx.query.filter(m.Tx.user_id.in_([h1_id, h2_id]),
                                     m.Tx.kind.in_(["pk_win", "pk_lose"])).count() == 2
            assert m.JOURNAL["JournalEntry"].query.filter_by(
                source="pk_payout", reference=str(pk_id)).count() == 1
    finally:
        with m.app.app_context():
            entry = m.JOURNAL["JournalEntry"].query.filter_by(
                source="pk_payout", reference=str(pk_id)).first()
            if entry:
                m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).delete()
                m.db.session.delete(entry)
            m.db.session.query(m.PKBattle).filter_by(id=pk_id).delete()
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([h1_id, h2_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([h1_id, h2_id])).delete(synchronize_session=False)
            m.db.session.commit()


def test_pk_gift_racing_with_finalization_is_either_paid_or_rejected(monkeypatch):
    """A PK gift racing settlement is either fully included in payout or fully rolled back."""
    with m.app.app_context():
        if m.db.engine.dialect.name != "postgresql":
            pytest.skip("requires PostgreSQL row-level update concurrency")
    marker = uuid4().hex[:10]
    with m.app.app_context():
        host1 = m.User(tg_id="pk-race-h1-" + marker, username="pk-host-1",
                       coins=0, real_coins=0, host_status="host")
        host2 = m.User(tg_id="pk-race-h2-" + marker, username="pk-host-2",
                       coins=0, real_coins=0, host_status="host")
        sender = m.User(tg_id="pk-race-s-" + marker, username="pk-sender",
                        coins=100, real_coins=100)
        m.db.session.add_all([host1, host2, sender])
        m.db.session.flush()
        pk = m.PKBattle(host1_id=host1.id, host2_id=host2.id,
                        status="active", started_at=m.now(), duration=300)
        m.db.session.add(pk)
        m.db.session.commit()
        pk_id, h1_id, h2_id, sender_id = pk.id, host1.id, host2.id, sender.id
        host_token, sender_token = m.sign(h1_id), m.sign(sender_id)

    barrier = Barrier(2)

    def gift():
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post("/api/pk/gift", json={"pk_id": pk_id, "to_host": 1, "gift": "rose", "qty": 10},
                           headers={"Authorization": "Bearer " + sender_token})

    def end():
        client = m.app.test_client()
        barrier.wait(timeout=10)
        return client.post("/api/pk/end", json={"pk_id": pk_id},
                           headers={"Authorization": "Bearer " + host_token})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            gift_future = pool.submit(gift)
            end_future = pool.submit(end)
            gift_response = gift_future.result(timeout=30)
            end_response = end_future.result(timeout=30)
        assert end_response.status_code == 200
        assert gift_response.status_code in (200, 404, 409)
        with m.app.app_context():
            saved_pk = m.db.session.get(m.PKBattle, pk_id)
            saved_sender = m.db.session.get(m.User, sender_id)
            saved_h1 = m.db.session.get(m.User, h1_id)
            saved_h2 = m.db.session.get(m.User, h2_id)
            assert saved_pk.status == "ended"
            if gift_response.status_code == 200:
                assert saved_pk.host1_gifts == 100
                assert saved_pk.host1_real == 100
                assert saved_sender.coins == 0
                assert saved_h1.earnings == 70
                assert saved_h2.earnings == 20
                assert m.Tx.query.filter_by(user_id=sender_id, kind="pk_gift").count() == 1
            else:
                assert saved_pk.host1_gifts == 0
                assert saved_pk.host1_real == 0
                assert saved_sender.coins == 100
                assert saved_h1.earnings == 0
                assert saved_h2.earnings == 0
                assert m.Tx.query.filter_by(user_id=sender_id, kind="pk_gift").count() == 0
    finally:
        with m.app.app_context():
            ids = [pk_id]
            entry_ids = [x.id for x in m.JOURNAL["JournalEntry"].query.filter(
                m.JOURNAL["JournalEntry"].source.in_(["pk_gift", "pk_payout"]),
                m.JOURNAL["JournalEntry"].reference.in_([str(pk_id)])
            ).all()]
            # PK gift entries reference the Tx id; remove payout by PK id and gift via source rows.
            gift_tx_ids = [x.id for x in m.Tx.query.filter_by(user_id=sender_id, kind="pk_gift").all()]
            gift_entries = m.JOURNAL["JournalEntry"].query.filter_by(source="pk_gift").all()
            entry_ids += [x.id for x in gift_entries if x.reference in {str(xid) for xid in gift_tx_ids}]
            if entry_ids:
                m.db.session.query(m.JOURNAL["JournalLine"]).filter(
                    m.JOURNAL["JournalLine"].entry_id.in_(set(entry_ids))).delete(synchronize_session=False)
                m.db.session.query(m.JOURNAL["JournalEntry"]).filter(
                    m.JOURNAL["JournalEntry"].id.in_(set(entry_ids))).delete(synchronize_session=False)
            m.db.session.query(m.PKBattle).filter_by(id=pk_id).delete()
            m.db.session.query(m.Tx).filter(m.Tx.user_id.in_([h1_id, h2_id, sender_id])).delete(synchronize_session=False)
            m.db.session.query(m.User).filter(m.User.id.in_([h1_id, h2_id, sender_id])).delete(synchronize_session=False)
            m.db.session.commit()
