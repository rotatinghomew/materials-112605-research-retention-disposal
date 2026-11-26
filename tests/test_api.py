"""HTTP API 与 partner.py 对接方回放的端到端测试。"""
import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from http.client import HTTPConnection
from pathlib import Path

from retention import RetentionService, load_bundle, parse_time
from retention.api import serve
from retention.exchange import ReceiptUnavailable, fetch_receipts

from support import FixtureCase, ROOT


class ApiTests(FixtureCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "fixtures" / "exchange.json")
                          .read_text(encoding="utf-8"))
        cls.bundle = load_bundle(data)
        cls.service = RetentionService(cls.bundle)
        cls.server = serve(cls.service, "127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        # 复用类级 bundle，跳过 FixtureCase 的重复装载
        pass

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def post_json(self, path, body):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health(self):
        status, payload = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_holds_and_recovery_endpoints(self):
        at = urllib.parse.quote("2027-01-18T14:00:00+08:00")
        _, payload = self.get_json(f"/v1/holds?at={at}")
        ids = {h["id"] for h in payload["holds"]}
        self.assertEqual(ids, {"H-A", "H-C"})
        _, rec = self.get_json(f"/v1/holds/H-A/recovery?at={at}")
        self.assertTrue(rec["conclusions"])

    def test_worklist_endpoint(self):
        at = urllib.parse.quote("2027-01-18T14:00:00+08:00")
        status, payload = self.get_json(
            f"/v1/stations/A/worklist?at={at}")
        self.assertEqual(status, 200)
        self.assertIn("disposal_options", payload)

    def test_ticket_post_rejected_under_hold(self):
        status, payload = self.post_json("/v1/tickets", {
            "ticket_id": "WEB-1", "sample_id": "S-2", "station": "A",
            "destination": "C", "mass": 100,
            "requested": "2027-01-18T14:00:00+08:00"})
        self.assertEqual(status, 409)
        self.assertIn("保全", payload["message"])

    def test_split_post_creates_identity(self):
        status, payload = self.post_json("/v1/splits", {
            "new_id": "WEB-S1", "parent_id": "S-1", "mass": 120,
            "at": "2027-01-18T14:00:00+08:00", "initial_site": "A"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["sample"], "WEB-S1")


import urllib.error
import urllib.parse
from urllib.error import HTTPError


class PartnerIntegrationTests(FixtureCase):
    """起一个真实的 partner.py 回放进程，验证同步与 503 语义。"""

    def _start_partner(self, *extra):
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "partner.py"), "--port", "0",
             "--data", str(ROOT / "fixtures" / "exchange.json"), *extra],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        line = proc.stdout.readline().strip()
        self.assertTrue(line.startswith("http://"), line)

        def cleanup():
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(cleanup)
        return line

    def test_fetch_and_sync_receipts(self):
        base = self._start_partner()
        remote = fetch_receipts(
            base, parse_time("2027-01-18T23:59:00+08:00"))
        self.assertTrue(remote)
        svc = RetentionService(self.bundle)
        result = svc.sync_receipts(remote)
        # 夹具里的回执已在 bundle 中：全部幂等跳过
        self.assertEqual(set(result["skipped"]),
                         {r["id"] for r in remote})

    def test_unavailable_raises_not_empty(self):
        base = self._start_partner("--unavailable")
        with self.assertRaises(ReceiptUnavailable):
            fetch_receipts(
                base, parse_time("2027-01-18T23:59:00+08:00"))


if __name__ == "__main__":
    unittest.main()
