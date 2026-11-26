"""处置申请：保全优先、复核需求/替代样、重复签单、时间与去向约束。"""
from retention.disposal import (
    assert_ticket_allowed, disposal_options, station_worklist,
)
from retention.model import DomainError, Ticket

from support import FixtureCase


def ticket(tid, sample, station, dest, mass, requested):
    from retention import parse_time
    return Ticket(id=tid, sample=sample, station=station, destination=dest,
                  mass=mass, requested=parse_time(requested))


class DisposalRuleTests(FixtureCase):
    AT = "2027-01-18T14:00:00+08:00"

    def test_hold_blocks_ticket_even_when_expired(self):
        # S-6 的 RP3 11:00 已到期，但 H-C 11:30 保全：按保全办理
        with self.assertRaises(DomainError) as ctx:
            assert_ticket_allowed(
                self.bundle,
                ticket("DIS-X", "S-6", "A", "C", 100, self.AT))
        self.assertIn("保全", str(ctx.exception))

    def test_exact_claim_protects_remaining_piece(self):
        # S-2 被 H-A 保全；选一块未被保全但有 exact 引用的场景：
        # 构造新报告对 S-1 的 exact 要求
        svc = self._service()
        svc.register_report(
            "RP6", "ARC", "T", 1, [["S-1", "exact", 3500]],
            "2027-01-18T08:00:00+08:00", "2027-01-18T18:00:00+08:00")
        with self.assertRaises(DomainError):
            assert_ticket_allowed(
                svc.bundle,
                ticket("DIS-X", "S-1", "A", "C", 1000, self.AT))

    def test_origin_claim_allows_any_peer_as_substitute(self):
        # S-3 有 origin 要求 1800，同源总量 10000；销毁 S-1 的 1000
        # 可由 S-2/S-3/S-5 等同源留样替代，S-1 本身无未结清单
        kind = assert_ticket_allowed(
            self.bundle,
            ticket("DIS-X", "S-1", "A", "C", 1000,
                   "2027-01-18T10:40:00+08:00"))
        self.assertEqual(kind, "destroy")

    def test_origin_shortfall_blocks_disposal(self):
        svc = self._service()
        # 把同源组要求提高到超过组内总量
        svc.register_report(
            "RP7", "ARC", "T", 1, [["S-5", "origin", 99999]],
            "2027-01-18T08:00:00+08:00", "2027-01-18T18:00:00+08:00")
        with self.assertRaises(DomainError):
            assert_ticket_allowed(
                svc.bundle,
                ticket("DIS-X", "S-5", "B", "C", 100, self.AT))

    def test_origin_reserve_is_precise_not_all_or_nothing(self):
        svc = self._service()
        # 新建一块无任何处置单的独立来源样片 700mg，要求同源保留 500：
        # 可销毁上限应为 200，而不是全锁或全放
        svc.register_site("D", "异地保管室", "retain")
        from retention.model import Sample
        svc.bundle.samples["S-9"] = Sample(
            id="S-9", origin="SRC-Z", parent=None, mass=700,
            initial_site="D",
            earliest=self.t("2027-01-18T10:00:00+08:00"))
        svc.register_report(
            "RP8", "ARC", "T", 1, [["S-9", "origin", 500]],
            "2027-01-18T08:00:00+08:00", "2027-01-18T18:00:00+08:00")
        options = next(o for o in disposal_options(
            svc.bundle, self.t(self.AT)) if o["sample"] == "S-9")
        self.assertEqual(options["allowed_mass"], 200)
        # 200 可以申请，300 超出替代后余量
        self.assertEqual(assert_ticket_allowed(
            svc.bundle, ticket("OK1", "S-9", "D", "C", 200, self.AT)),
            "destroy")
        with self.assertRaises(DomainError):
            assert_ticket_allowed(
                svc.bundle, ticket("BAD1", "S-9", "D", "C", 300, self.AT))

    def test_open_ticket_blocks_second_ticket_for_same_sample(self):
        # DIS-4 已申请 S-5 1000 毫克且未出库
        with self.assertRaises(DomainError) as ctx:
            assert_ticket_allowed(
                self.bundle,
                ticket("DIS-X", "S-5", "B", "C", 100, self.AT))
        self.assertIn("不得重复签单", str(ctx.exception))

    def test_earliest_outbound_limit(self):
        with self.assertRaises(DomainError):
            assert_ticket_allowed(
                self.bundle,
                ticket("DIS-X", "S-1", "A", "C", 100,
                       "2027-01-18T09:00:00+08:00"))

    def test_mass_cannot_exceed_actual_on_hand(self):
        with self.assertRaises(DomainError):
            assert_ticket_allowed(
                self.bundle,
                ticket("DIS-X", "S-1", "A", "C", 99999, self.AT))

    def test_retain_to_retain_is_transfer_not_destroy(self):
        kind = assert_ticket_allowed(
            self.bundle,
            ticket("DIS-X", "S-1", "A", "B", 100, self.AT))
        self.assertEqual(kind, "transfer")

    def test_options_report_actual_on_hand_and_blocked_masses(self):
        options = disposal_options(self.bundle, self.t(self.AT))
        s2 = next(o for o in options if o["sample"] == "S-2")
        self.assertEqual(s2["on_hand_mass"], 1000)
        self.assertEqual(s2["allowed_mass"], 0)
        self.assertTrue(s2["held"])
        s1 = next(o for o in options if o["sample"] == "S-1")
        self.assertEqual(s1["allowed_mass"], 4000)

    def test_worklist_lists_options_pending_and_hold_tasks(self):
        wl = station_worklist(
            self.bundle, "A", self.t(self.AT))
        self.assertTrue(any(t["ticket"] == "DIS-3"
                            for t in wl["pending_tickets"]))
        self.assertTrue(any(task["sample"] == "S-2"
                            for task in wl["hold_tasks"]))

    def _service(self):
        from retention import RetentionService
        return RetentionService(self.bundle)
