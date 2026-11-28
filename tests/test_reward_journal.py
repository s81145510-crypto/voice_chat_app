"""Regression tests for promo reward double-entry journal postings."""
from datetime import datetime, timedelta

import pytest

from backend import main as m


@pytest.fixture
def reward_api():
    with m.app.app_context():
        m.db.create_all()
        tg_ids = ["reward-journal-daily", "reward-journal-mission", "reward-journal-fail"]
        old_users = m.User.query.filter(m.User.tg_id.in_(tg_ids)).all()
        old_ids = [u.id for u in old_users]
        if old_ids:
            m.MissionProgress.query.filter(m.MissionProgress.user_id.in_(old_ids)).delete(synchronize_session=False)
            m.Tx.query.filter(m.Tx.user_id.in_(old_ids)).delete(synchronize_session=False)
            entries = m.JOURNAL["JournalEntry"].query.filter(
                m.JOURNAL["JournalEntry"].idempotency_key.like("reward-test:%")
            ).all()
            entry_ids = [e.id for e in entries]
            if entry_ids:
                m.JOURNAL["JournalLine"].query.filter(
                    m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)
                ).delete(synchronize_session=False)
                m.JOURNAL["JournalEntry"].query.filter(
                    m.JOURNAL["JournalEntry"].id.in_(entry_ids)
                ).delete(synchronize_session=False)
            m.User.query.filter(m.User.id.in_(old_ids)).delete(synchronize_session=False)
            m.db.session.commit()

        yesterday = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=1)).strftime("%Y-%m-%d")
        users = [
            m.User(tg_id="reward-journal-daily", username="daily-test", coins=0, real_coins=0, last_daily=yesterday),
            m.User(tg_id="reward-journal-mission", username="mission-test", coins=0, real_coins=0),
            m.User(tg_id="reward-journal-fail", username="fail-test", coins=0, real_coins=0, last_daily=yesterday),
        ]
        m.db.session.add_all(users)
        m.db.session.commit()
        client = m.app.test_client()
        tokens = {u.tg_id: {"Authorization": "Bearer " + m.sign(u.id)} for u in users}
        m.db.session.add(m.MissionProgress(
            user_id=users[1].id, day=m.today(), key="join", count=1, claimed=False
        ))
        m.db.session.commit()
        yield client, users, tokens
        m.db.session.rollback()
        user_ids = [u.id for u in users]
        m.MissionProgress.query.filter(m.MissionProgress.user_id.in_(user_ids)).delete(synchronize_session=False)
        m.Tx.query.filter(m.Tx.user_id.in_(user_ids)).delete(synchronize_session=False)
        entries = m.JOURNAL["JournalEntry"].query.filter(
            m.JOURNAL["JournalEntry"].idempotency_key.like("daily:%")
            | m.JOURNAL["JournalEntry"].idempotency_key.like("mission:%")
            | m.JOURNAL["JournalEntry"].idempotency_key.like("reward-test:%")
        ).all()
        entry_ids = [e.id for e in entries]
        if entry_ids:
            m.JOURNAL["JournalLine"].query.filter(
                m.JOURNAL["JournalLine"].entry_id.in_(entry_ids)
            ).delete(synchronize_session=False)
            m.JOURNAL["JournalEntry"].query.filter(
                m.JOURNAL["JournalEntry"].id.in_(entry_ids)
            ).delete(synchronize_session=False)
        m.User.query.filter(m.User.id.in_(user_ids)).delete(synchronize_session=False)
        m.db.session.commit()


def _assert_promo_entry(m, key, user_id, expected):
    entry = m.JOURNAL["JournalEntry"].query.filter_by(idempotency_key=key).one()
    lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
    assert entry.currency == "COIN"
    assert sum(x.debit for x in lines) == expected
    assert sum(x.credit for x in lines) == expected
    promo = next(x for x in lines if x.account_code == "liability.promo_coin_balance")
    assert promo.user_id == user_id
    assert promo.credit == expected


def test_daily_reward_credits_wallet_and_promo_liability_atomically(reward_api):
    client, users, headers = reward_api
    user = users[0]
    response = client.post("/api/daily", headers=headers[user.tg_id])
    assert response.status_code == 200
    assert response.json["reward"] > 0
    m.db.session.refresh(user)
    assert user.coins == response.json["reward"]
    _assert_promo_entry(m, f"daily:{user.id}:{m.today()}:COIN", user.id, response.json["reward"])


def test_mission_reward_credits_wallet_and_promo_liability_atomically(reward_api):
    client, users, headers = reward_api
    user = users[1]
    response = client.post("/api/missions/claim", json={"key": "join"}, headers=headers[user.tg_id])
    assert response.status_code == 200
    assert response.json["got"] == 5
    m.db.session.refresh(user)
    assert user.coins == 5
    _assert_promo_entry(m, f"mission:{user.id}:{m.today()}:join:COIN", user.id, 5)


def test_failed_daily_journal_rolls_back_wallet_and_claim(monkeypatch, reward_api):
    client, users, headers = reward_api
    user = users[2]
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("journal unavailable")))
    response = client.post("/api/daily", headers=headers[user.tg_id])
    assert response.status_code == 500
    m.db.session.refresh(user)
    assert user.coins == 0
    assert user.last_daily != m.today()
