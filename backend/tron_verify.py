"""Read-only verification for confirmed USDT TRC20 deposits on TRON mainnet.

This module never signs, broadcasts, or transfers funds. It only verifies a submitted
transaction against TRON's solidified transaction body and execution receipt.
"""
import json
import re
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class TronVerificationError(Exception):
    pass


class TronVerificationPending(TronVerificationError):
    pass


def _base58check_payload(address):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not isinstance(address, str) or not address:
        raise ValueError("Missing TRON address")
    number = 0
    for char in address:
        if char not in alphabet:
            raise ValueError("Invalid TRON address")
        number = number * 58 + alphabet.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(address) - len(address.lstrip("1"))
    raw = b"\x00" * pad + raw
    if len(raw) < 5:
        raise ValueError("Invalid TRON address")
    payload, checksum = raw[:-4], raw[-4:]
    import hashlib
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected or len(payload) != 21 or payload[0] != 0x41:
        raise ValueError("Invalid TRON mainnet address")
    return payload.hex()


def _post_json(base_url, path, txid, api_key="", timeout=12):
    body = json.dumps({"value": txid}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise TronVerificationPending("TRON network is temporarily unavailable; retry verification") from exc


def verify_usdt_trc20_payment(tx_hash, receiving_address, expected_usdt,
                              api_url="https://api.trongrid.io", api_key=""):
    """Return verified transfer details or raise Pending/VerificationError.

    Expected USDT is compared at six-decimal token precision. A solidified body and
    solidified successful receipt are both required; missing data is pending, not failure.
    """
    txid = str(tx_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", txid):
        raise TronVerificationError("Invalid transaction hash")
    try:
        receiver_hex = _base58check_payload(receiving_address)[2:].lower()
        expected_units = int((Decimal(str(expected_usdt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 1_000_000).to_integral_exact())
    except (ValueError, InvalidOperation, ArithmeticError) as exc:
        raise TronVerificationError("Invalid receiving address or expected amount") from exc
    if expected_units <= 0:
        raise TronVerificationError("Expected USDT amount must be positive")

    body = _post_json(api_url, "/walletsolidity/gettransactionbyid", txid, api_key)
    if not body or str(body.get("txID", "")).lower() != txid:
        raise TronVerificationPending("Transaction is not yet confirmed on the solidified TRON chain")

    contracts = (body.get("raw_data") or {}).get("contract") or []
    if len(contracts) != 1 or contracts[0].get("type") != "TriggerSmartContract":
        raise TronVerificationError("Transaction is not a single TRC20 contract transfer")
    value = ((contracts[0].get("parameter") or {}).get("value") or {})
    contract_hex = str(value.get("contract_address") or "").lower().removeprefix("0x")
    contract_hex = contract_hex[-40:]
    expected_contract_hex = _base58check_payload(USDT_TRC20_CONTRACT)[2:].lower()
    if contract_hex != expected_contract_hex:
        raise TronVerificationError("Transaction does not call the official USDT TRC20 contract")

    calldata = str(value.get("data") or "").lower().removeprefix("0x")
    if len(calldata) != 136 or calldata[:8] != "a9059cbb":
        raise TronVerificationError("Transaction is not a standard USDT transfer")
    call_to = calldata[8:72][-40:]
    call_amount = int(calldata[72:136], 16)
    if call_to != receiver_hex or call_amount != expected_units:
        raise TronVerificationError("On-chain recipient or amount does not match this purchase")

    receipt = _post_json(api_url, "/walletsolidity/gettransactioninfobyid", txid, api_key)
    if not receipt or str(receipt.get("id", "")).lower() != txid:
        raise TronVerificationPending("Solidified execution receipt is not available yet")
    if receipt.get("result") == "FAILED" or (receipt.get("receipt") or {}).get("result") != "SUCCESS":
        raise TronVerificationError("TRON reports that this transaction execution failed")

    matching = False
    for log in receipt.get("log") or []:
        log_contract = str(log.get("address") or "").lower().removeprefix("0x")[-40:]
        topics = [str(topic).lower().removeprefix("0x").zfill(64) for topic in (log.get("topics") or [])]
        if (log_contract == expected_contract_hex and len(topics) >= 3
                and topics[0] == TRANSFER_TOPIC and topics[2][-40:] == receiver_hex
                and int(str(log.get("data") or "0"), 16) == expected_units):
            matching = True
            break
    if not matching:
        raise TronVerificationError("Successful receipt has no matching USDT Transfer event to the configured wallet")

    return {
        "verified": True,
        "tx_hash": txid,
        "amount_usdt": str(Decimal(expected_units) / Decimal(1_000_000)),
        "receiver": receiving_address,
        "block_number": receipt.get("blockNumber"),
        "block_timestamp": receipt.get("blockTimeStamp"),
    }
