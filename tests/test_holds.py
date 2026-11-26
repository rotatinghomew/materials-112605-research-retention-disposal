"""保全：范围解析、释放链、拆分延续、跨站确认。"""
from retention import holds as hr
from retention.model import (
    ClaimMode, DomainError, HoldScope, HoldState,
)
from retention.ledger import replay

from support import FixtureCase


class HoldScopeTests(FixtureCase):
    def test_sample_hold_covers_listed_and_aliquots_only(self):
        hold = self.bundle.holds["H-C"]
        covered = hr.covered_samples(self.bundle, hold)
        self.assertEqual(covered, {"S-4", "S-6"})

    def test_report_hold_does_not_expand_to_origin_peers(self):
        # H-B 针对 RP2（claim S-3 origin）；只覆盖 S-3 及其等分，
        # 不能扩大到同 origin 的 S-1/S-2/S-5
        hold = self.bundle.holds["H-B"]
        covered = hr.covered_samples(self.bundle, hold)
        self.assertEqual(covered, {"S-3"})

    def test_split_preserves_hold_relationship(self):
        svc = self._service()
        svc.split_sample("S-3a", "S-3", 400,
                         "2027-01-18T09:00:00+08:00", initial_site="B")
        covered = hr.covered_samples(svc.bundle, self.bundle.holds["H-B"])
        self.assertIn("S-3a", covered)
        self.assertIn("S-3", covered)
        # 母样账面扣减，旧身份仍在
        self.assertEqual(svc.bundle.samples["S-3"].mass, 1600)
        self.assertEqual(svc.bundle.samples["S-3a"].parent, "S-3")

    def test_release_targets_only_referenced_hold(self):
        at = self.t("2027-01-18T14:00:00+08:00")
        open_ids = {h.id for h in hr.open_holds(self.bundle, at)}
        # H-D 释放 H-B；H-A、H-C 是独立保全，继续履行
        self.assertNotIn("H-B", open_ids)
        self.assertIn("H-A", open_ids)
        self.assertIn("H-C", open_ids)

    def test_independent_hold_survives_unrelated_release(self):
        svc = self._service()
        # 模拟误绑 H-A 后撤回；H-C 不受影响
        svc.release_hold("REL-X", "LAW", "H-A",
                         "2027-01-18T12:00:00+08:00")
        at = self.t("2027-01-18T12:30:00+08:00")
        open_ids = {h.id for h in hr.open_holds(svc.bundle, at)}
        self.assertNotIn("H-A", open_ids)
        self.assertIn("H-C", open_ids)
        self.assertIn("H-B", open_ids)

    def test_effective_time_not_arrival_drives_hold(self):
        # 10:59 时 H-B 尚未生效（effective 10:15? 是已生效）——
        # 改用 H-A：effective 11:00，10:59 未生效
        self.assertFalse(hr.is_held(
            self.bundle, "S-2", self.t("2027-01-18T10:59:00+08:00")))
        self.assertTrue(hr.is_held(
            self.bundle, "S-2", self.t("2027-01-18T11:00:00+08:00")))

    def test_site_tasks_are_scoped_to_site_inventory(self):
        ledger = replay(self.bundle)
        at = self.t("2027-01-18T14:00:00+08:00")
        tasks_a = hr.site_hold_tasks(self.bundle, ledger, "A", at)
        tasks_b = hr.site_hold_tasks(self.bundle, ledger, "B", at)
        samples_a = {t["sample"] for t in tasks_a}
        samples_b = {t["sample"] for t in tasks_b}
        self.assertIn("S-2", samples_a)
        self.assertIn("S-6", samples_a)
        self.assertNotIn("S-2", samples_b)
        self.assertIn("S-4", samples_b)

    def _service(self):
        from retention import RetentionService
        return RetentionService(self.bundle)
