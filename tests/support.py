"""测试公共装载。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention import load_bundle, parse_time  # noqa: E402

FIXTURE = ROOT / "fixtures" / "exchange.json"


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.bundle = load_bundle(self.data)

    @staticmethod
    def t(text):
        return parse_time(text)
