# -*- coding: utf-8 -*-
"""Money and reward rules from the Twenty One Party master specification.

Pure functions only: no database, no Flask. All amounts are integers
(coins, or the smallest money unit); percentages use Decimal.
Connected to the app: upline() and accrue() (7-generation commission).
The activity reward and exchange functions are not connected yet.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

# ---------- ordinary gifts (spec 4.1, 4.2). PK and Lucky gifts are separate products ----------
HOST_RATE = Decimal('0.50')
GENERATION_RATES = tuple(Decimal(r) for r in ('0.05', '0.025', '0.015', '0.01', '0.0075', '0.0075', '0.005'))
POOL_RATE = Decimal('0.12')
assert sum(GENERATION_RATES) == POOL_RATE


def _int_amount(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('amount must be a whole number, 0 or more')
    return value


def _floor(value, rate):
    return int((Decimal(value) * rate).to_integral_value(rounding=ROUND_DOWN))


def upline(earner_id, parent_of, depth=len(GENERATION_RATES)):
    """Referral chain above earner_id, nearest first. Stops at a loop."""
    chain, seen, current = [], {earner_id}, parent_of(earner_id)
    while current and len(chain) < depth and current not in seen:
        chain.append(current)
        seen.add(current)
        current = parent_of(current)
    return chain


def split_gift(value, chain=(), eligible=lambda user_id: True):
    """Host 50%, seven generations 12% of the ORIGINAL value, platform the rest.

    Rounding policy: every share is rounded down; whatever is left (including
    missing or ineligible generations) stays with the platform. So the parts
    always add up to value and the generations can never exceed 12%.
    """
    value = _int_amount(value)
    if len(chain) > len(GENERATION_RATES) or len(set(chain)) != len(chain):
        raise ValueError('bad referral chain')
    host = _floor(value, HOST_RATE)
    commissions = []
    for generation, (user_id, rate) in enumerate(zip(chain, GENERATION_RATES), start=1):
        amount = _floor(value, rate) if eligible(user_id) else 0   # no moving up: G3 stays G3
        if amount:
            commissions.append((generation, user_id, amount))
    paid = sum(c[2] for c in commissions)
    reserve = max(0, _floor(value, POOL_RATE) - paid)               # unallocated part of the 12%
    return dict(host=host, commissions=commissions, commission_total=paid,
                reserve=reserve, platform=value - host - paid - reserve)


# Small gifts would earn 0 if each commission were rounded on its own, so each person's
# part-coins are carried over. 1 unit = 1/10,000 coin; rates are exact in these units.
UNITS_PER_COIN = 10000
GENERATION_UNITS = tuple(int(rate * UNITS_PER_COIN) for rate in GENERATION_RATES)


def accrue(base, chain, eligible, carry_of):
    """Commission on `base` purchased coins.

    Returns [(generation, user_id, whole_coins_to_pay_now, new_carry_units)].
    Over any number of gifts a person is never paid more than their exact rate.
    """
    base = _int_amount(base)
    if len(chain) > len(GENERATION_UNITS) or len(set(chain)) != len(chain):
        raise ValueError('bad referral chain')
    plan = []
    for generation, (user_id, rate_units) in enumerate(zip(chain, GENERATION_UNITS), start=1):
        if base and eligible(user_id):
            whole, carry = divmod(int(carry_of(user_id)) + base * rate_units, UNITS_PER_COIN)
            plan.append((generation, user_id, whole, carry))
    return plan


# ---------- host activity rewards (spec 5) ----------
EAT = timezone(timedelta(hours=3))          # Africa/Addis_Ababa has no daylight saving
BLOCK_SECONDS = 60 * 60
MAX_REWARDS_PER_DAY = 2
WELCOME_REWARDS = (100, 120, 140, 160, 180, 200, 250)
LEVEL_REWARDS = ((5, 50), (10, 65), (20, 80), (30, 100), (50, 125))
TOP_LEVEL_REWARD = 150

# Cash-out policy: reference value only; actual payout remains subject to admin approval.
WITHDRAW_ETB_PER_COIN = Decimal('0.10')  # 1,000 coins = 100 ETB
MIN_WITHDRAW_ETB = 1000
MIN_WITHDRAW_COINS = int((Decimal(MIN_WITHDRAW_ETB) / WITHDRAW_ETB_PER_COIN).to_integral_value(rounding='ROUND_UP'))
MIN_GIFT_EARNINGS_SHARE = Decimal('0.50')


def withdrawal_quote(coins):
    coins = _int_amount(coins)
    if coins < MIN_WITHDRAW_COINS:
        raise ValueError(f'minimum withdrawal is {MIN_WITHDRAW_ETB} ETB ({MIN_WITHDRAW_COINS} coins)')
    birr = int((Decimal(coins) * WITHDRAW_ETB_PER_COIN).to_integral_value(rounding=ROUND_DOWN))
    return dict(coins=coins, birr=birr, rate=str(WITHDRAW_ETB_PER_COIN))


def withdrawal_source_split(coins, gift_earnings, commission):
    coins = _int_amount(coins)
    gift_earnings = _int_amount(gift_earnings)
    commission = _int_amount(commission)
    min_gift = (coins + 1) // 2
    if gift_earnings < min_gift:
        raise ValueError('at least 50% of the withdrawal must come from genuine gift earnings')
    if gift_earnings + commission < coins:
        raise ValueError('not enough withdrawable income')
    commission_part = min(commission, coins - min_gift)
    earnings_part = coins - commission_part
    if earnings_part > gift_earnings:
        raise ValueError('not enough genuine gift earnings')
    return dict(earnings=earnings_part, commission=commission_part)


def merge_intervals(intervals):
    """Joins overlapping (start, end) times so no second is counted twice."""
    merged = []
    for start, end in sorted((int(s), int(e)) for s, e in intervals if e > s):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(x) for x in merged]


def eligible_seconds_by_day(intervals):
    """Splits verified Voice + Live hosting time into Ethiopia calendar days.

    The caller passes only verified, active hosting time (no listening, AFK,
    disconnected or background time).
    """
    days = {}
    for start, end in merge_intervals(intervals):
        while start < end:
            local = datetime.fromtimestamp(start, EAT)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            cut = min(end, int(midnight.timestamp()))
            key = local.date().isoformat()
            days[key] = days.get(key, 0) + cut - start
            start = cut
    return days


def rewards_earned(seconds):
    return min(MAX_REWARDS_PER_DAY, max(0, int(seconds)) // BLOCK_SECONDS)


def reward_per_block(day_number, host_level):
    """day_number starts at 1. Days 1-7 use the welcome table, later days the host level."""
    if day_number < 1 or host_level < 1:
        raise ValueError('day number and host level start at 1')
    if day_number <= len(WELCOME_REWARDS):
        return WELCOME_REWARDS[day_number - 1]
    return next((coins for top, coins in LEVEL_REWARDS if host_level <= top), TOP_LEVEL_REWARD)

WITHDRAWAL_TRANSITIONS = {
    ('pending', 'under_review'): 'under_review',
    ('pending', 'reject'): 'rejected',
    ('under_review', 'approve'): 'approved',
    ('under_review', 'reject'): 'rejected',
    ('approved', 'processing'): 'processing',
    ('approved', 'reject'): 'rejected',
    ('processing', 'paid'): 'paid',
}


def withdrawal_next_status(current, action):
    return WITHDRAWAL_TRANSITIONS.get((str(current), str(action)))


# ---------- income -> coins (spec 6). There is deliberately no reverse function ----------
EXCHANGE_FEE_RATE = Decimal('0.05')


def exchange_quote(income_minor, coins_per_unit, minor_per_unit=100):
    """income_minor: income in santim. coins_per_unit: admin rate, coins for 1 ETB."""
    income_minor = _int_amount(income_minor)
    if isinstance(coins_per_unit, float):
        raise TypeError('use Decimal or a string for the rate, not float')
    rate = Decimal(coins_per_unit)
    if not rate.is_finite() or rate <= 0:
        raise ValueError('exchange rate must be greater than zero')
    if income_minor == 0:
        raise ValueError('amount must be greater than zero')
    fee = int((income_minor * EXCHANGE_FEE_RATE).to_integral_value(rounding=ROUND_HALF_UP))
    net = income_minor - fee
    coins = int((Decimal(net) / minor_per_unit * rate).to_integral_value(rounding=ROUND_DOWN))
    if coins < 1:
        raise ValueError('amount is too small')
    return dict(gross=income_minor, fee=fee, net=net, rate=str(rate), coins=coins)
