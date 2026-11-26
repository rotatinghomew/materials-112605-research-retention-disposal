"""叙述中的组合场景：更正、误绑撤回、替代样、跨站保全的端到端串联。"""
from retention import RetentionService
from retention.model import DomainError
from retention.disposal import disposal_options
from retention.reports import active_reports
from retention import holds as hr

from support import FixtureCase


class NarrativeScenarioTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.svc = RetentionService(self.bundle)

    def test_correction_frees_old_sample_but_other_claims_remain(self):
        # RP4 在 11:10 更正 RP1；S-2 不再为 RP1 所需，但 H-A 仍保全 S-2
        at = self.t("2027-01-18T12:00:00+08:00")
        active = {r.id for r in active_reports(self.bundle, at)}
        self.assertIn("RP4", active)
        self.assertNotIn("RP1", active)
        options = {o["sample"]: o
                   for o in disposal_options(self.bundle, at)}
        # 即使报告不再引用，保全仍冻结
        self.assertEqual(options["S-2"]["allowed_mass"], 0)
        self.assertTrue(any(b["reason"] == "hold"
                            for b in options["S-2"]["blocked"]))

    def test_misbound_hold_withdrawn_keeps_independent_hold(self):
        # 法务承认 H-C 对 S-4 误绑并撤回；S-6 上的独立保全仍须履行
        self.svc.release_hold(
            "REL-C4", "LAW", "H-C",
            "2027-01-18T12:30:00+08:00")
        at = self.t("2027-01-18T13:00:00+08:00")
        coverage = hr.hold_coverage(self.svc.bundle, at)
        # H-C 整条释放（S-4、S-6 都不再由 H-C 覆盖）
        self.assertNotIn("S-4", coverage)
        self.assertNotIn("S-6", coverage)
        # 再来一条独立保全绑 S-6，撤回 H-C 不影响它
        self.svc.place_hold(
            "H-E", "LAW", "sample", ["S-6"],
            "2027-01-18T13:10:00+08:00")
        later = self.t("2027-01-18T13:20:00+08:00")
        coverage2 = hr.hold_coverage(self.svc.bundle, later)
        self.assertIn("S-6", coverage2)
        self.assertEqual([h.id for h in coverage2["S-6"]], ["H-E"])

    def test_origin_substitute_keeps_review_possible_after_disposal(self):
        # RP2 要求 SRC-P 同源 1800；即使 S-3 的 DIS-5 2000 毫克将来销毁，
        # S-1/S-2/S-5 仍有充足同源替代
        svc = self.svc
        # 先把 H-B 释放后的 S-3 处置链走完（H-D 已在 13:15 释放 H-B）
        svc.record_receipt("R-D5a", "DIS-5", "B", "dispatched", 2000,
                           "2027-01-18T13:40:00+08:00",
                           "2027-01-18T13:41:00+08:00")
        svc.record_receipt("R-D5b", "DIS-5", "C", "accepted", 2000,
                           "2027-01-18T13:50:00+08:00",
                           "2027-01-18T13:51:00+08:00")
        svc.record_receipt("R-D5c", "DIS-5", "C", "destroyed", 2000,
                           "2027-01-18T13:55:00+08:00",
                           "2027-01-18T14:30:00+08:00")
        from retention.reports import claim_satisfaction
        from retention.ledger import replay
        ledger = replay(svc.bundle)
        rows = claim_satisfaction(
            svc.bundle, ledger, self.t("2027-01-18T14:45:00+08:00"))
        rp2 = next(r for r in rows if r["report"] == "RP2")
        self.assertTrue(rp2["satisfied"])
        # S-3 已全部销毁，条目以 0 质量保留；同源其他样片顶上
        self.assertEqual(rp2["by_sample"]["S-3"], 0)
        self.assertGreaterEqual(
            sum(v for k, v in rp2["by_sample"].items() if k != "S-3"), 1800)

    def test_split_under_hold_creates_held_child(self):
        # 被保全的 S-2 仍可拆分（检验需要），拆出的新标签自动受保全
        self.svc.split_sample(
            "S-2a", "S-2", 200,
            "2027-01-18T12:00:00+08:00", initial_site="A")
        at = self.t("2027-01-18T12:05:00+08:00")
        self.assertTrue(hr.is_held(self.svc.bundle, "S-2a", at))
        with self.assertRaises(DomainError):
            self.svc.submit_ticket(
                "T-H", "S-2a", "A", "C", 100,
                "2027-01-18T12:10:00+08:00")

    def test_dispatch_receipt_never_counts_as_destruction(self):
        # 只有 destroyed 回执才减少可复核量；dispatched/accepted 都不减少
        from retention.ledger import replay
        svc = self.svc
        before = replay(svc.bundle).position("S-1").existing_mass
        svc.submit_ticket("T-D", "S-1", "A", "C", 300,
                          "2027-01-18T12:00:00+08:00")
        svc.record_receipt("R-D1", "T-D", "A", "dispatched", 300,
                           "2027-01-18T12:05:00+08:00")
        svc.record_receipt("R-D2", "T-D", "C", "accepted", 300,
                           "2027-01-18T12:10:00+08:00",
                           "2027-01-18T12:15:00+08:00")
        after = replay(svc.bundle).position("S-1")
        self.assertEqual(after.existing_mass, before)
        self.assertEqual(after.destroyed, 0)
        self.assertEqual(after.at_destination, 300)
