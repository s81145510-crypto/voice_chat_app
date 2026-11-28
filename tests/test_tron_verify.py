import pytest

from backend import tron_verify as tv


TXID = "a" * 64
RECEIVER = "TUTGyjoRFaqviTtejUnXe92BcU8z3BaTUE"


def _fixture(monkeypatch, amount=1230000, recipient=None):
    receiver_hex = "ab" * 20
    contract_hex = "cd" * 20
    to_hex = recipient or receiver_hex
    monkeypatch.setattr(
        tv, "_base58check_payload",
        lambda address: "41" + (receiver_hex if address == RECEIVER else contract_hex),
    )
    calldata = "a9059cbb" + ("0" * 24) + to_hex + format(amount, "064x")
    body = {
        "txID": TXID,
        "raw_data": {"contract": [{
            "type": "TriggerSmartContract",
            "parameter": {"value": {
                "contract_address": "41" + contract_hex,
                "data": calldata,
            }},
        }]},
    }
    receipt = {
        "id": TXID,
        "result": "SUCCESS",
        "receipt": {"result": "SUCCESS"},
        "blockNumber": 123,
        "blockTimeStamp": 456,
        "log": [{
            "address": contract_hex,
            "topics": [
                tv.TRANSFER_TOPIC,
                "0" * 64,
                "0" * 24 + to_hex,
            ],
            "data": format(amount, "064x"),
        }],
    }
    monkeypatch.setattr(tv, "_post_json", lambda base, path, txid, key: body if path.endswith("gettransactionbyid") else receipt)


def test_verifies_solidified_usdt_transfer(monkeypatch):
    _fixture(monkeypatch)
    result = tv.verify_usdt_trc20_payment(TXID, RECEIVER, "1.23")
    assert result["verified"] is True
    assert result["amount_usdt"] == "1.23"
    assert result["block_number"] == 123


def test_rejects_wrong_recipient(monkeypatch):
    _fixture(monkeypatch, recipient="ef" * 20)
    with pytest.raises(tv.TronVerificationError, match="recipient or amount"):
        tv.verify_usdt_trc20_payment(TXID, RECEIVER, "1.23")


def test_rejects_wrong_amount(monkeypatch):
    _fixture(monkeypatch, amount=1220000)
    with pytest.raises(tv.TronVerificationError, match="recipient or amount"):
        tv.verify_usdt_trc20_payment(TXID, RECEIVER, "1.23")


def test_pending_when_transaction_not_solidified(monkeypatch):
    monkeypatch.setattr(tv, "_base58check_payload", lambda address: "41" + "ab" * 20)
    monkeypatch.setattr(tv, "_post_json", lambda *args: {})
    with pytest.raises(tv.TronVerificationPending):
        tv.verify_usdt_trc20_payment(TXID, RECEIVER, "1.23")


def test_rejects_malformed_hash():
    with pytest.raises(tv.TronVerificationError, match="Invalid transaction hash"):
        tv.verify_usdt_trc20_payment("not-a-hash", RECEIVER, "1.23")
