"""服务层写命令：拆分、登记、保全、处置单、回执链校验、幂等同步。"""
from retention import RetentionService
from retention.model import DomainError

from support import FixtureCase


class ServiceCommandTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.svc = RetentionService(self.bundle)

    def test_split_deducts_parent_and_keeps_origin(self):
        child = self.svc.split_sample(
            "S-1a", "S-1", 500, "2027-01-18T12:00:00+08:00",
            initial_site="B")
        self.assertEqual(child.origin, "SRC-P")
        self.assertEqual(self.svc.bundle.samples["S-1"].mass, 3500)
        self.assertEqual(self.svc.bundle.samples["S-1a"].mass, 500)

    def test_split_id_cannot_be_reused(self):
        self.svc.split_sample(
            "S-1a", "S-1", 100, "2027-01-18T12:00:00+08:00")
        with self.assertRaises(DomainError):
            self.svc.split_sample(
                "S-1a", "S-1", 100, "2027-01-18T12:05:00+08:00")

    def test_split_beyond_existing_mass_rejected(self):
        with self.assertRaises(DomainError):
            self.svc.split_sample(
                "S-1x", "S-1", 99999, "2027-01-18T12:00:00+08:00")

    def test_receipt_chain_cannot_skip_dispatch(self):
        self.svc.submit_ticket(
            "T9", "S-1", "A", "C", 200,
            "2027-01-18T12:00:00+08:00")
        with self.assertRaises(DomainError):
            self.svc.record_receipt(
                "RX", "T9", "C", "destroyed", 200,
                "2027-01-18T12:30:00+08:00")

    def test_destroy_requires_destroy_capability(self):
        self.svc.submit_ticket(
            "T9", "S-1", "A", "C", 200,
            "2027-01-18T12:00:00+08:00")
        self.svc.record_receipt("R9a", "T9", "A", "dispatched", 200,
                                "2027-01-18T12:05:00+08:00")
        self.svc.record_receipt("R9b", "T9", "C", "accepted", 200,
                                "2027-01-18T12:10:00+08:00")
        with self.assertRaises(DomainError):
            self.svc.record_receipt("R9c", "T9", "A", "destroyed", 200,
                                    "2027-01-18T12:15:00+08:00")

    def test_hold_prevents_ticket_submission(self):
        with self.assertRaises(DomainError):
            self.svc.submit_ticket(
                "T8", "S-2", "A", "C", 100,
                "2027-01-18T12:00:00+08:00")

    def test_sync_is_idempotent_and_creates_no_ticket(self):
        remote = [
            {"id": "RC1", "ticket": "DIS-1", "sample": "S-2", "actor": "A",
             "state": "dispatched", "mass": 500,
             "happened": "2027-01-18T10:50:00+08:00",
             "arrived": "2027-01-18T10:52:00+08:00"},
            {"id": "RNEW", "ticket": "DIS-4", "sample": "S-5", "actor": "B",
             "state": "dispatched", "mass": 1000,
             "happened": "2027-01-18T14:10:00+08:00",
             "arrived": "2027-01-18T14:12:00+08:00"},
        ]
        first = self.svc.sync_receipts(remote)
        second = self.svc.sync_receipts(remote)
        self.assertEqual(set(first["added"]), {"RNEW"})
        self.assertEqual(second["added"], [])
        self.assertIn("RNEW", second["skipped"])
        # 同步回执不会自动新开处置单
        self.assertEqual(len(self.svc.bundle.tickets),
                         len(self.data["tickets"]))

    def test_export_roundtrip(self):
        exported = self.svc.export_json()
        reloaded = RetentionService.from_json(
            __import__("json").dumps(exported))
        self.assertEqual(set(reloaded.bundle.samples),
                         set(self.svc.bundle.samples))
