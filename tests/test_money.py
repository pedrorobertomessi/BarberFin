import unittest
from decimal import Decimal

from app.errors import MoneyError
from app.money import format_brl, loads_decimal, to_cents


class MoneyTests(unittest.TestCase):
    def test_reais_para_centavos(self):
        self.assertEqual(to_cents(194), 19400)
        self.assertEqual(to_cents(Decimal("0.66")), 66)
        self.assertEqual(to_cents(Decimal("19.9")), 1990)
        self.assertEqual(to_cents("45.00"), 4500)

    def test_float_e_rejeitado(self):
        with self.assertRaises(MoneyError):
            to_cents(0.1 + 0.2)
        with self.assertRaises(MoneyError):
            to_cents(True)

    def test_sem_arredondamento_silencioso(self):
        for bad in ("10.005", Decimal("0.001"), "1e-3"):
            with self.assertRaises(MoneyError):
                to_cents(bad)

    def test_invalidos(self):
        for bad in (-1, "-0.01", "abc", "NaN", "Infinity", None, [], 10**14):
            with self.assertRaises(MoneyError):
                to_cents(bad)

    def test_json_decimal_sem_float(self):
        d = loads_decimal(b'{"a": 0.66, "b": 194, "c": 1.10}')
        self.assertIsInstance(d["a"], Decimal)
        self.assertEqual(to_cents(d["a"]), 66)
        self.assertEqual(to_cents(d["c"]), 110)

    def test_soma_exata(self):
        total = sum(to_cents(loads_decimal(b"0.10")) for _ in range(1000))
        self.assertEqual(total, 10000)   # com float seria 99.99999...

    def test_formatacao_brl(self):
        self.assertEqual(format_brl(0), "R$ 0,00")
        self.assertEqual(format_brl(5), "R$ 0,05")
        self.assertEqual(format_brl(4500), "R$ 45,00")
        self.assertEqual(format_brl(123456), "R$ 1.234,56")
        self.assertEqual(format_brl(100000000), "R$ 1.000.000,00")
        with self.assertRaises(Exception):
            format_brl(-1)


if __name__ == "__main__":
    unittest.main()
