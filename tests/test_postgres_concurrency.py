"""PostgreSQL-only integration tests for concurrent financial requests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from backend import main as m


pytestmark = pytest.mark.skipif(
    m.db.engine.dialect.name != "postgresql",
    reason="requires PostgreSQL row-level locking",
)


def test_two_simultaneous_withdrawals_cannot_overdraw_earnings(monkeypatch):
    assert m.db.engine.dialect.name == "postgresql"
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
