import pytest

from backend.payment_refs import normalize_payment_reference


def test_normalizes_trc20_hash_case_and_whitespace():
    raw = '  ' + ('A' * 64) + '  '
    assert normalize_payment_reference('usdt', raw) == 'a' * 64


@pytest.mark.parametrize('raw', ['', 'a' * 63, 'a' * 65, 'g' * 64, '0x' + 'a' * 64])
def test_rejects_invalid_trc20_hash(raw):
    with pytest.raises(ValueError):
        normalize_payment_reference('usdt', raw)


def test_normalizes_telebirr_reference():
    assert normalize_payment_reference('telebirr', ' ab-123_xy ') == 'AB-123_XY'


@pytest.mark.parametrize('raw', ['', 'abc', 'A' * 41, 'bad ref', 'abc/123'])
def test_rejects_invalid_telebirr_reference(raw):
    with pytest.raises(ValueError):
        normalize_payment_reference('telebirr', raw)


def test_rejects_unknown_method():
    with pytest.raises(ValueError):
        normalize_payment_reference('bank', '123456')
