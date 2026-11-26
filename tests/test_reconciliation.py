"""班次切换三态追回、迟到销毁冲减、保全生效时间判定。"""
from retention.reconciliation import (
    classify_at_hold, confirmations_overview,
    late_destruction_reconciliation,
)
from retention.model import ReceiptState

from support import FixtureCase


class ReconciliationTests(FixtureCase):
    def test_three_location_conclusions_for_shift_gap(self):
        # H-A：S-2 站内 1000 + 已销毁 500（生效前）
        conclusions = classify_at_hold(self.bundle, self.bundle.holds["H-A"])
        cats = {(c.category, c.action): c.mass for c in conclusions}
        self.assertEqual(cats[("on_site", "freeze_location")], 1000)
        self.assertEqual(cats[("destroyed", "write_off")], 500)

    def test_in_transit_and_pre_received_demand_recovery(self):
        svc = self._service()
        # 新样片走完整在途链，再下保全
        svc.submit_ticket(
            "T1", "S-1", "A", "C", 300,
            "2027-01-18T12:00:00+08:00")
        svc.record_receipt(
            "R1", "T1", "A", "dispatched", 300,
            "2027-01-18T12:05:00+08:00")
        svc.place_hold(
            "H-T", "LAW", "sample", ["S-1"],
            "2027-01-18T12:10:00+08:00")
        conclusions = classify_at_hold(svc.bundle, svc.bundle.holds["H-T"])
        transit = [c for c in conclusions if c.category == "in_transit"]
        self.assertEqual(len(transit), 1)
        self.assertEqual(transit[0].action, "intercept")

        # 处置点预接收后：结论变为 demand_return
        svc.record_receipt(
            "R2", "T1", "C", "accepted", 300,
            "2027-01-18T12:12:00+08:00",
            "2027-01-18T13:00:00+08:00")
        conclusions2 = classify_at_hold(svc.bundle, svc.bundle.holds["H-T"])
        pre = [c for c in conclusions2 if c.category == "pre_received"]
        self.assertEqual(pre[0].action, "demand_return")

    def test_destruction_after_hold_is_breach_not_write_off(self):
        svc = self._service()
        # S-6 无任何报告引用，但被 H-C 保全；另取 S-1 走完整链
        svc.submit_ticket(
            "T2", "S-1", "A", "C", 200,
            "2027-01-18T12:00:00+08:00")
        svc.record_receipt("R3", "T2", "A", "dispatched", 200,
                           "2027-01-18T12:05:00+08:00")
        svc.record_receipt("R4", "T2", "C", "accepted", 200,
                           "2027-01-18T12:08:00+08:00")
        svc.place_hold("H-U", "LAW", "sample", ["S-1"],
                       "2027-01-18T12:10:00+08:00")
        # 保全生效后仍销毁
        svc.record_receipt("R5", "T2", "C", "destroyed", 200,
                           "2027-01-18T12:20:00+08:00",
                           "2027-01-18T13:00:00+08:00")
        conclusions = classify_at_hold(svc.bundle, svc.bundle.holds["H-U"])
        breach = [c for c in conclusions if c.action == "breach_investigation"]
        self.assertEqual(len(breach), 1)
        self.assertEqual(breach[0].mass, 200)

    def test_late_destruction_writeoff_preserves_basis_and_review(self):
        result = late_destruction_reconciliation(
            self.bundle, self.bundle.holds["H-A"],
            at=self.t("2027-01-18T14:00:00+08:00"))
        self.assertEqual(result["total_written_off"], 500)
        basis = result["write_offs"][0]["basis"]
        self.assertEqual(basis["allowed_mass"], 500)
        self.assertEqual(basis["destination"], "C")
        # 冲减后 RP4(origin S-5) 仍满足；S-2 自身 exact 的 RP1 已被更正
        self.assertTrue(result["review_still_sufficient"])

    def test_knowledge_gap_shows_who_must_confirm_without_new_ticket(self):
        overview = confirmations_overview(self.bundle)
        dis3 = next(r for r in overview if r["ticket"] == "DIS-3")
        self.assertTrue(dis3["duplicate_ticket_blocked"])
        actors = {d["actor"] for d in dis3["confirmations_due"]}
        self.assertIn("A", actors)

    def test_knowledge_at_models_shift_change(self):
        # 11:16 只知道 S-2 已预接收，不知销毁；保全视角应是 pre_received
        conclusions = classify_at_hold(
            self.bundle, self.bundle.holds["H-A"],
            knowledge_at=self.t("2027-01-18T11:18:00+08:00"))
        pre = [c for c in conclusions if c.category == "pre_received"]
        self.assertEqual(pre[0].mass, 500)
        self.assertFalse(any(c.category == "destroyed" for c in conclusions))

    def _service(self):
        from retention import RetentionService
        return RetentionService(self.bundle)
