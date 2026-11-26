"""台账回放：质量守恒、回执事实分离、缺口可见但不补单。"""
from retention import load_bundle
from retention.ledger import replay
from retention.model import DomainError

from support import FixtureCase


class LedgerTests(FixtureCase):
    def test_mass_conservation(self):
        ledger = replay(self.bundle)
        for sample_id, sample in self.bundle.samples.items():
            pos = ledger.position(sample_id)
            total = (sum(pos.on_hand.values()) + pos.in_transit
                     + pos.at_destination + pos.destroyed)
            self.assertEqual(total, sample.mass, sample_id)

    def test_dispatched_accepted_destroyed_are_distinct_facts(self):
        ledger = replay(self.bundle)
        flow = ledger.flow("DIS-1")
        self.assertEqual(flow.stage, "destroyed")
        self.assertEqual(flow.dispatched, 500)
        self.assertEqual(flow.accepted, 500)
        self.assertEqual(flow.destroyed, 500)
        pos = ledger.position("S-2")
        self.assertEqual(pos.destroyed, 500)
        self.assertEqual(pos.existing_mass, 1000)
        self.assertEqual(pos.on_hand_at("A"), 1000)

    def test_incomplete_chain_records_gap_without_inventing_stock(self):
        ledger = replay(self.bundle)
        # DIS-2：有 dispatched 和 returned，缺 accepted；退回不能凭空入账
        flow2 = ledger.flow("DIS-2")
        self.assertTrue(flow2.open)
        self.assertTrue(flow2.inconsistencies)
        self.assertTrue(any("预接收" in g for g in ledger.position("S-4").gaps))
        self.assertEqual(ledger.position("S-4").in_transit, 500)
        # DIS-3：缺 dispatched 直接 accepted，700 毫克不进处置点库存
        flow3 = ledger.flow("DIS-3")
        self.assertTrue(flow3.open)
        self.assertTrue(any("出库" in g for g in ledger.position("S-6").gaps))
        self.assertEqual(ledger.position("S-6").on_hand_at("A"), 700)

    def test_confirmations_due_names_actor_and_action(self):
        ledger = replay(self.bundle)
        due = {(d["actor"], d["action"]): d["mass"]
               for d in ledger.flow("DIS-3").confirmations_due()}
        self.assertEqual(due[("A", "dispatched")], 700)
        due2 = {(d["actor"], d["action"]): d["mass"]
                for d in ledger.flow("DIS-4").confirmations_due()}
        self.assertEqual(due2[("B", "dispatched")], 1000)

    def test_knowledge_at_ignores_later_arrivals(self):
        # 11:18 时 RC3(arrived 11:23) 尚未到达，S-2 应仍显示处置点在手
        ledger = replay(
            self.bundle,
            knowledge_at=self.t("2027-01-18T11:18:00+08:00"))
        pos = ledger.position("S-2")
        self.assertEqual(pos.at_destination, 500)
        self.assertEqual(pos.destroyed, 0)
        full = replay(self.bundle)
        self.assertEqual(full.position("S-2").destroyed, 500)

    def test_bundle_validation_rejects_bad_reference(self):
        data = {**self.data, "tickets": [
            dict(self.data["tickets"][0], sample="NOPE")]}
        with self.assertRaises(DomainError):
            load_bundle(data)

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            self.t("2027-01-18T10:00:00")
