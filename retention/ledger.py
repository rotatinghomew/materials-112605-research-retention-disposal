"""实物台账：按业务发生时间回放处置单与回执，重建每个拆分件的去向。

质量桶（毫克）：
- on_hand[site]      仍在某保管/处置站内、可清点的质量
- in_transit         已出库但处置点尚未预接收
- at_destination     处置点预接收、尚未销毁或退回
- destroyed          已确认销毁，仅存在于历史回执

回执只确认各自事实：dispatched 是出库送达的起点，destroyed 才是销毁；
两者绝不能合并。记录不全时记入 gaps，并在处置单上标明待确认方，
缺口不会被当作可再开单的可用量，也不会自动补出第二张处置单。
"""
from dataclasses import dataclass, field

from .model import ReceiptState, SiteCapability


@dataclass
class TicketFlow:
    ticket_id: str
    sample_id: str
    station: str
    destination: str
    requested_mass: int
    to_destroy: bool = False
    dispatched: int = 0
    accepted: int = 0
    destroyed: int = 0
    returned: int = 0
    last_happened: object = None
    missing: list = field(default_factory=list)
    inconsistencies: list = field(default_factory=list)

    @property
    def stage(self):
        if self.destroyed:
            return "destroyed"
        if self.returned and not self.accepted and not self.in_transit_mass:
            return "returned"
        if self.returned:
            return "partly_returned"
        if self.accepted and not self.to_destroy:
            # 保管点间交接：接收点已入库即结清
            return "received"
        if self.accepted:
            return "pre_received"
        if self.dispatched:
            return "in_transit"
        return "drafted"

    @property
    def at_destination_mass(self):
        return max(0, self.accepted - self.destroyed - self.returned)

    @property
    def in_transit_mass(self):
        return max(0, self.dispatched - self.accepted - self.returned)

    @property
    def open(self):
        settled = self.destroyed + self.returned
        # 交接给保管点且已接收的份额也算结清
        if self.accepted and not self.to_destroy:
            settled = max(settled, self.accepted + self.returned)
        return settled < self.requested_mass

    def confirmations_due(self):
        """按当前证据链，列出还欠确认的经手方与动作。"""
        due = []
        if self.dispatched < self.requested_mass:
            due.append({"actor": self.station, "action": "dispatched",
                        "mass": self.requested_mass - self.dispatched})
        if self.accepted < self.dispatched and not self.returned:
            due.append({"actor": self.destination, "action": "accepted",
                        "mass": self.dispatched - self.accepted})
        open_at_destination = self.accepted - self.destroyed - self.returned
        if open_at_destination > 0 and self.to_destroy:
            due.append({"actor": self.destination,
                        "action": "destroyed_or_returned",
                        "mass": open_at_destination})
        return due


@dataclass
class Position:
    sample_id: str
    on_hand: dict = field(default_factory=dict)   # site -> mass
    in_transit: int = 0
    at_destination: int = 0
    destroyed: int = 0
    gaps: list = field(default_factory=list)

    @property
    def existing_mass(self):
        """仍可复核的实物质量（不含已销毁）。"""
        return sum(self.on_hand.values()) + self.in_transit + self.at_destination

    def on_hand_at(self, site):
        return self.on_hand.get(site, 0)


@dataclass
class Ledger:
    positions: dict
    flows: dict

    def position(self, sample_id):
        return self.positions[sample_id]

    def flow(self, ticket_id):
        return self.flows[ticket_id]

    def open_tickets_for(self, sample_id):
        return [f for f in self.flows.values()
                if f.sample_id == sample_id and f.open]


def _take(bucket, mass, flow, note, gaps):
    """从质量桶扣减；证据不足时只扣到 0 并登记缺口，不允许负库存。"""
    available = bucket if isinstance(bucket, int) else None
    if available is not None:
        if available < mass:
            gaps.append(f"{flow.sample_id}: {note} 需要 {mass}，账面仅有 {available}")
            mass = available
        return mass
    return mass


def replay(bundle, knowledge_at=None):
    """回放资料。knowledge_at 给定时只采用 arrived <= 该时刻的回执，
    用于模拟班次切换时“手上看得到的证据”。"""
    receipts = list(bundle.receipts.values())
    if knowledge_at is not None:
        receipts = [r for r in receipts if r.arrived <= knowledge_at]
    receipts.sort(key=lambda r: (r.happened, r.id))

    positions = {
        sid: Position(sample_id=sid,
                      on_hand={sample.initial_site: sample.mass})
        for sid, sample in bundle.samples.items()
    }
    flows = {
        tid: TicketFlow(
            ticket_id=tid, sample_id=t.sample,
            station=t.station, destination=t.destination,
            requested_mass=t.mass,
            to_destroy=bundle.sites[t.destination].capability
            is SiteCapability.DESTROY)
        for tid, t in bundle.tickets.items()
    }

    for receipt in receipts:
        ticket = bundle.tickets[receipt.ticket]
        flow = flows[receipt.ticket]
        pos = positions[receipt.sample]
        mass = receipt.mass
        flow.last_happened = receipt.happened

        if receipt.state is ReceiptState.DISPATCHED:
            moved = _take(pos.on_hand.get(flow.station, 0), mass, flow,
                          "出库", pos.gaps)
            if moved < mass:
                flow.inconsistencies.append(
                    f"{receipt.happened.isoformat()} 出库 {mass} 超出在站量")
            pos.on_hand[flow.station] = pos.on_hand.get(flow.station, 0) - moved
            if pos.on_hand[flow.station] == 0:
                pos.on_hand.pop(flow.station, None)
            pos.in_transit += moved
            flow.dispatched += moved

        elif receipt.state is ReceiptState.ACCEPTED:
            available = pos.in_transit
            moved = min(mass, available)
            if moved < mass:
                flow.inconsistencies.append(
                    f"{receipt.happened.isoformat()} 预接收 {mass}，"
                    f"在途仅 {available}（出库回执不全）")
                pos.gaps.append(
                    f"{receipt.sample}: 预接收 {mass} 缺少对应出库份额，"
                    f"仅 {moved} 毫克可入账")
            pos.in_transit -= moved
            destination = bundle.sites[flow.destination]
            if destination.capability is SiteCapability.DESTROY:
                # 处置点预接收：待销毁/退回，不计入任何保管站库位
                pos.at_destination += moved
            else:
                # 保管点之间交接：接收点入库，可继续清点与复核
                pos.on_hand[flow.destination] = (
                    pos.on_hand.get(flow.destination, 0) + moved)
            flow.accepted += moved

        elif receipt.state is ReceiptState.DESTROYED:
            actor = bundle.sites[receipt.actor]
            if actor.capability is not SiteCapability.DESTROY:
                flow.inconsistencies.append(
                    f"{receipt.actor} 无销毁权能，销毁回执 {receipt.id} 不能采信")
                pos.gaps.append(
                    f"{receipt.sample}: 无销毁权能站点 {receipt.actor} 的销毁回执")
                continue
            moved = _take(pos.at_destination, mass, flow, "销毁", pos.gaps)
            if moved < mass:
                flow.inconsistencies.append(
                    f"{receipt.happened.isoformat()} 销毁 {mass} 超出处置点在手量")
            pos.at_destination -= moved
            pos.destroyed += moved
            flow.destroyed += moved

        elif receipt.state is ReceiptState.RETURNED:
            available = pos.at_destination
            moved = min(mass, available)
            if moved < mass:
                flow.inconsistencies.append(
                    f"{receipt.happened.isoformat()} 退回 {mass}，"
                    f"处置点在手仅 {available}（预接收回执不全）")
                pos.gaps.append(
                    f"{receipt.sample}: 退回 {mass} 缺少对应的预接收份额，"
                    f"仅 {moved} 毫克可入账")
            pos.at_destination -= moved
            pos.on_hand[flow.station] = pos.on_hand.get(flow.station, 0) + moved
            flow.returned += moved

    # 汇总每张处置单还欠谁的确认
    for flow in flows.values():
        flow.missing = [
            {"actor": item["actor"], "action": item["action"], "mass": item["mass"]}
            for item in flow.confirmations_due()
        ]

    return Ledger(positions=positions, flows=flows)
