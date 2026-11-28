"""Payment reference normalization and validation helpers."""
import re


def normalize_payment_reference(method, raw):
    value = str(raw or '').strip()
    if method == 'usdt':
        value = value.lower()
        if not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('Enter a valid 64-character TRC20 transaction hash')
        return value
    if method == 'telebirr':
        value = value.upper()
        if len(value) < 6 or len(value) > 40 or not re.fullmatch(r'[A-Z0-9_-]+', value):
            raise ValueError('Enter a valid Telebirr transaction ID (6-40 letters, numbers, _ or -)')
        return value
    raise ValueError('Unsupported payment method')
