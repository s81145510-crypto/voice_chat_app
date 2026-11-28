"""COIN journal regression tests for single-player games and P2P escrow."""
import pytest

from backend import main as m


@pytest.fixture
def game_api():
    with m.app.app_context():
        m.db.create_all()
        tg_ids = ["journal-game-a", "journal-game-b", "journal-game-c"]
        old = m.User.query.filter(m.User.tg_id.in_(tg_ids)).all()
        old_ids = [u.id for u in old]
        if old_ids:
            m.Tx.query.filter(m.Tx.user_id.in_(old_ids)).delete(synchronize_session=False)
            m.P2P.query.filter(m.or_(m.P2P.creator_id.in_(old_ids))).delete(synchronize_session=False)
            entries = m.JOURNAL["JournalEntry"].query.filter(
                m.JOURNAL["JournalEntry"].created_by.in_(tg_ids)
            ).all()
            eids = [e.id for e in entries]
            if eids:
                m.JOURNAL["JournalLine"].query.filter(m.JOURNAL["JournalLine"].entry_id.in_(eids)).delete(synchronize_session=False)
                m.JOURNAL["JournalEntry"].query.filter(m.JOURNAL["JournalEntry"].id.in_(eids)).delete(synchronize_session=False)
            m.User.query.filter(m.User.id.in_(old_ids)).delete(synchronize_session=False)
            m.db.session.commit()
        users = [
            m.User(tg_id=tg_ids[0], username="game-a", coins=100, real_coins=100),
            m.User(tg_id=tg_ids[1], username="game-b", coins=100, real_coins=100),
            m.User(tg_id=tg_ids[2], username="game-c", coins=100, real_coins=100),
        ]
        m.db.session.add_all(users)
        m.db.session.commit()
        client = m.app.test_client()
        headers = {u.tg_id: {"Authorization": "Bearer " + m.sign(u.id)} for u in users}
        yield client, users, headers
        m.db.session.rollback()
        ids = [u.id for u in users]
        m.Tx.query.filter(m.Tx.user_id.in_(ids)).delete(synchronize_session=False)
        m.P2P.query.filter(m.P2P.creator_id.in_(ids)).delete(synchronize_session=False)
        entries = m.JOURNAL["JournalEntry"].query.filter(
            m.JOURNAL["JournalEntry"].created_by.in_(tg_ids)
        ).all()
        eids = [e.id for e in entries]
        if eids:
            m.JOURNAL["JournalLine"].query.filter(m.JOURNAL["JournalLine"].entry_id.in_(eids)).delete(synchronize_session=False)
            m.JOURNAL["JournalEntry"].query.filter(m.JOURNAL["JournalEntry"].id.in_(eids)).delete(synchronize_session=False)
        m.User.query.filter(m.User.id.in_(ids)).delete(synchronize_session=False)
        m.db.session.commit()


def _entry(key):
    entry = m.JOURNAL["JournalEntry"].query.filter_by(idempotency_key=key).one()
    lines = m.JOURNAL["JournalLine"].query.filter_by(entry_id=entry.id).all()
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines)
    assert entry.currency == "COIN"
    return {x.account_code: x for x in lines}


def test_single_player_loss_posts_stake_and_house_margin(game_api, monkeypatch):
    client, users, headers = game_api
    user = users[0]
    monkeypatch.setattr(m.rnd, "random", lambda: 0.99)
    response = client.post("/api/games/flip", json={"bet": 10, "pick": 1}, headers=headers[user.tg_id])
    assert response.status_code == 200
    assert response.json["payout"] == 0
    m.db.session.refresh(user)
    assert user.coins == 90 and user.real_coins == 90
    tx = m.Tx.query.filter_by(user_id=user.id, kind="game").order_by(m.Tx.id.desc()).first()
    lines = _entry(f"game:{tx.id}:COIN")
    assert lines["liability.user_coin_balance"].debit == 10
    assert lines["revenue.platform_game"].credit == 10


def test_single_player_win_journals_payout_above_stake(game_api, monkeypatch):
    client, users, headers = game_api
    user = users[0]
    monkeypatch.setattr(m.rnd, "random", lambda: 0.0)
    response = client.post("/api/games/flip", json={"bet": 10, "pick": 1}, headers=headers[user.tg_id])
    assert response.status_code == 200
    assert response.json["payout"] == 14
    m.db.session.refresh(user)
    assert user.coins == 104 and user.real_coins == 90
    tx = m.Tx.query.filter_by(user_id=user.id, kind="game").order_by(m.Tx.id.desc()).first()
    lines = _entry(f"game:{tx.id}:COIN")
    assert lines["liability.user_coin_balance"].debit == 10
    assert lines["liability.promo_coin_balance"].credit == 14
    assert lines["expense.game_payout"].debit == 4


def test_game_journal_failure_rolls_back_wager_and_payout(game_api, monkeypatch):
    client, users, headers = game_api
    user = users[0]
    monkeypatch.setitem(m.JOURNAL, "post_internal", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("journal down")))
    response = client.post("/api/games/flip", json={"bet": 10, "pick": 1}, headers=headers[user.tg_id])
    assert response.status_code == 500
    m.db.session.refresh(user)
    assert user.coins == 100 and user.real_coins == 100
    assert m.Tx.query.filter_by(user_id=user.id, kind="game").count() == 0


def test_p2p_create_and_cancel_refunds_original_purchased_split(game_api):
    client, users, headers = game_api
    creator = users[0]
    created = client.post("/api/games/p2p/create", json={"bet": 20}, headers=headers[creator.tg_id])
    assert created.status_code == 200
    battle_id = created.json["id"]
    m.db.session.refresh(creator)
    assert creator.coins == 80 and creator.real_coins == 80
    create_lines = _entry(f"p2p:{battle_id}:create:COIN")
    assert create_lines["liability.user_coin_balance"].debit == 20
    assert create_lines["liability.p2p_escrow"].credit == 20

    canceled = client.post("/api/games/p2p/cancel", json={"id": battle_id}, headers=headers[creator.tg_id])
    assert canceled.status_code == 200
    m.db.session.refresh(creator)
    assert creator.coins == 100 and creator.real_coins == 100
    cancel_lines = _entry(f"p2p:{battle_id}:cancel:COIN")
    assert cancel_lines["liability.p2p_escrow"].debit == 20
    assert cancel_lines["liability.user_coin_balance"].credit == 20


def test_p2p_join_settles_both_stakes_and_platform_margin(game_api, monkeypatch):
    client, users, headers = game_api
    creator, joiner = users[0], users[1]
    created = client.post("/api/games/p2p/create", json={"bet": 10}, headers=headers[creator.tg_id])
    battle_id = created.json["id"]
    monkeypatch.setattr(m.rnd, "random", lambda: 0.0)
    joined = client.post("/api/games/p2p/join", json={"id": battle_id}, headers=headers[joiner.tg_id])
    assert joined.status_code == 200
    assert joined.json["win"] is True and joined.json["prize"] == 19
    m.db.session.refresh(creator)
    m.db.session.refresh(joiner)
    assert creator.coins == 90 and creator.real_coins == 90
    assert joiner.coins == 109 and joiner.real_coins == 90
    lines = _entry(f"p2p:{battle_id}:settle:COIN")
    assert lines["liability.p2p_escrow"].debit == 20
    assert lines["liability.promo_coin_balance"].credit == 19
    assert lines["revenue.platform_p2p"].credit == 1
