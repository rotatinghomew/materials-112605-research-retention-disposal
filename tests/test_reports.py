"""报告登记/更正、exact 与 origin 认可方式、复核视图。"""
from retention.reports import (
    active_reports, archive_view, claim_satisfaction, required_mass_by_sample,
)
from retention.ledger import replay
from retention.model import ClaimMode, DomainError

from support import FixtureCase


class ReportTests(FixtureCase):
    def test_supersession_takes_over_from_new_valid_from(self):
        before = {r.id for r in active_reports(
            self.bundle, self.t("2027-01-18T11:05:00+08:00"))}
        after = {r.id for r in active_reports(
            self.bundle, self.t("2027-01-18T11:30:00+08:00"))}
        self.assertIn("RP1", before)
        self.assertNotIn("RP1", after)
        self.assertIn("RP4", after)

    def test_expired_report_drops_out(self):
        ids = {r.id for r in active_reports(
            self.bundle, self.t("2027-01-18T15:30:00+08:00"))}
        self.assertNotIn("RP2", ids)
        self.assertNotIn("RP4", ids)

    def test_exact_claim_only_accepts_named_identity(self):
        ledger = replay(self.bundle)
        rows = claim_satisfaction(
            self.bundle, ledger, self.t("2027-01-18T10:00:00+08:00"))
        rp1 = next(r for r in rows if r["report"] == "RP1")
        self.assertEqual(rp1["family"], ["S-2"])
        self.assertEqual(rp1["mode"], "exact")
        # S-2 剩余 1000 >= min 1000
        self.assertTrue(rp1["satisfied"])

    def test_origin_claim_pools_same_origin_samples(self):
        ledger = replay(self.bundle)
        rows = claim_satisfaction(
            self.bundle, ledger, self.t("2027-01-18T10:30:00+08:00"))
        rp2 = next(r for r in rows if r["report"] == "RP2")
        self.assertEqual(rp2["mode"], "origin")
        self.assertEqual(set(rp2["family"]), {"S-1", "S-2", "S-3", "S-5"})
        self.assertTrue(rp2["satisfied"])

    def test_required_mass_uses_max_not_sum_across_reports(self):
        svc = self._service()
        # 再登记一份同样 exact 指向 S-2、要求更高的报告
        svc.register_report(
            "RP5", "ARC", "TEAM-X", 1, [["S-2", "exact", 800]],
            "2027-01-18T08:00:00+08:00", "2027-01-18T18:00:00+08:00")
        exact_need, _ = required_mass_by_sample(
            svc.bundle, self.t("2027-01-18T10:00:00+08:00"))
        # RP1 要求 1000，RP5 要求 800：取最大值 1000 而非 1800
        self.assertEqual(exact_need["S-2"], 1000)

    def test_correction_keeps_old_identity_traceable(self):
        view = archive_view(self.bundle,
                            self.t("2027-01-18T12:00:00+08:00"))
        rp4 = next(r for r in view["reports"] if r["report"] == "RP4")
        self.assertEqual(rp4["replaces"], "RP1")
        self.assertEqual(rp4["claims"][0]["claim_sample"], "S-5")

    def test_report_referencing_missing_sample_rejected(self):
        svc = self._service()
        with self.assertRaises(DomainError):
            svc.register_report(
                "RPX", "ARC", "T", 1, [["NOPE", "exact", 10]],
                "2027-01-18T08:00:00+08:00", "2027-01-18T18:00:00+08:00")

    def _service(self):
        from retention import RetentionService
        return RetentionService(self.bundle)
