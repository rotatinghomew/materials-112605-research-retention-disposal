"""核对交换资料中的实体引用与原始回执顺序。"""
from datetime import datetime
import json
from pathlib import Path
import unittest

from partner import parse_time as time

ROOT = Path(__file__).resolve().parent


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "exchange.json").read_text(encoding="utf-8"))

    def test_record_identity(self):
        for name, collection in self.data.items():
            with self.subTest(collection=name):
                ids = [rec["id"] for rec in collection]
                self.assertTrue(ids)
                self.assertTrue(all(isinstance(i, str) and i for i in ids))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(set(x) == set(collection[0]) for x in collection))

    def test_receipt_clocks(self):
        for rec in self.data["receipts"]:
            with self.subTest(receipt=rec["id"]):
                self.assertLessEqual(time(rec["happened"]), time(rec["arrived"]))
        with self.assertRaises(ValueError):
            time(datetime(2027, 1, 18).isoformat())

    def test_related_records(self):
        def row(collection, key):
            return next(rec for rec in self.data[collection] if rec["id"] == key)
        assert row("samples","S-2")["parent"] == row("samples","S-3")["parent"]
        assert sum(x["mass"] for x in self.data["samples"] if x["parent"] == "S-1") < row("samples","S-1")["mass"]
        assert row("reports","RP4")["replaces"] == "RP1"
        assert row("holds","H-D")["references"] == ["H-B"]
        assert row("holds","H-C")["references"] != row("holds","H-B")["references"]
        original = row("receipts","RC3")
        assert row("tickets",original["ticket"])["sample"] in row("holds","H-A")["references"]
        assert time(original["happened"]) < time(row("holds","H-A")["effective"]) < time(original["arrived"])
