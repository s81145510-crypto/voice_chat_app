"""Reads the numbers used by the live code and checks that no purchased coin
can create more withdrawable value than it was sold for.
It does NOT cover hosting, payment or tax costs, fraud or refunds."""
import ast, os, unittest
from decimal import Decimal as D

ROOT = os.path.join(os.path.dirname(__file__), '..', 'backend')


def constants(filename, names):
    found = {}
    for node in ast.parse(open(os.path.join(ROOT, filename), encoding='utf-8').read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            targets, values = [node.targets[0]], [node.value]
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
            targets, values = node.targets[0].elts, node.value.elts
        else:
            continue
        for t, v in zip(targets, values):
            if t.id in names:
                found[t.id] = ast.literal_eval(v)
    return found


M = constants('main.py', {'COIN_BIRR', 'PACKAGES', 'HOST_SHARE', 'SHARE', 'DEFAULT_SHARE', 'PK_WINNER_SHARE', 'PK_LOSER_SHARE'})
SELL = constants('seller.py', {'TIERS', 'PRICE_FLOOR'})
d = lambda x: D(str(x))
GENERATIONS = D('0.12')


class NoLossPerGift(unittest.TestCase):
    cheapest = min([d(b) / d(c) for c, b in M['PACKAGES']] + [d(t[2]) for t in SELL['TIERS']])

    def test_cheapest_coin(self):
        self.assertEqual(self.cheapest, D('0.40'))

    def test_ordinary_gift_keeps_38_percent(self):
        highest = max([d(M['HOST_SHARE']), d(M['DEFAULT_SHARE'])] + [d(v) for v in M['SHARE'].values()])
        self.assertLessEqual(highest + GENERATIONS, D('0.62'))
        cost = (highest + GENERATIONS) * d(M['COIN_BIRR'])
        self.assertGreaterEqual(self.cheapest - cost, self.cheapest * D('0.38'))

    def test_pk_stays_positive(self):          # thinner: 10% at the cheapest price
        cost = (d(M['PK_WINNER_SHARE']) + d(M['PK_LOSER_SHARE'])) * d(M['COIN_BIRR'])
        self.assertGreaterEqual(self.cheapest - cost, self.cheapest * D('0.10'))


if __name__ == '__main__':
    unittest.main()
