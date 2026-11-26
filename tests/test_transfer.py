"""保管点之间交接：接收点入库即结清，不进入处置点质量桶。"""
from retention import RetentionService
from retention.ledger import replay

from support import FixtureCase


class TransferTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.svc = RetentionService(self.bundle)

    def test_retain_to_retain_receipt_lands_in_receiver_site(self):
        self.svc.submit_ticket(
            "TR1", "S-1", "A", "B", 300,
            "2027-01-18T12:00:00+08:00")
        self.svc.record_receipt(
            "TR1a", "TR1", "A", "dispatched", 300,
            "2027-01-18T12:05:00+08:00")
        self.svc.record_receipt(
            "TR1b", "TR1", "B", "accepted", 300,
            "2027-01-18T12:20:00+08:00",
            "2027-01-18T12:25:00+08:00")
        ledger = replay(self.svc.bundle)
        pos = ledger.position("S-1")
        self.assertEqual(pos.on_hand, {"A": 3700, "B": 300})
        self.assertEqual(pos.at_destination, 0)
        self.assertEqual(pos.in_transit, 0)
        flow = ledger.flow("TR1")
        self.assertEqual(flow.stage, "received")
        self.assertFalse(flow.open)
        self.assertEqual(flow.confirmations_due(), [])
        # 质量守恒
        self.assertEqual(sum(pos.on_hand.values()), 4000)

    def test_destroy_pre_receive_stays_out_of_site_stock(self):
        ledger = replay(self.bundle)
        # DIS-1 已销毁；RC2 预接收阶段曾在处置点桶，最终落 destroyed
        pos = ledger.position("S-2")
        self.assertEqual(pos.at_destination, 0)
        self.assertEqual(pos.destroyed, 500)
