"""保全与处置的时间对账。

场景：处置点预接收后没来得及回复就切换班次，保全消息在空档到达。
依据“当时在手的实物和收到的保全范围”区分三种结论：
- 尚在站内：冻结库位、撤回处置单；
- 在途（含处置点预接收未销毁）：拦截/要求退回；
- 已销毁：核对销毁业务时间是否早于保全生效。

迟到回执证实处置点在保全生效前已完成销毁时，从可复核量中冲减实际
销毁份额，标明复核还够不够，同时保留处置所依据的时间与允许范围。
"""
from dataclasses import dataclass

from .ledger import replay
from . import holds as hold_rules
from .reports import claim_satisfaction


@dataclass
class LocationConclusion:
    sample: str
    hold: str
    category: str          # on_site / in_transit / pre_received / destroyed
    mass: int
    action: str
    detail: str


def classify_at_hold(bundle, hold, knowledge_at=None):
    """按保全生效时（或当前所知证据）实物所在位置分类。"""
    ledger = replay(bundle, knowledge_at=knowledge_at)
    conclusions = []
    for sample_id in sorted(hold_rules.covered_samples(bundle, hold)):
        pos = ledger.position(sample_id)
        for site, mass in sorted(pos.on_hand.items()):
            if bundle.sites[site].capability.value == "destroy":
                conclusions.append(LocationConclusion(
                    sample_id, hold.id, "pre_received", mass,
                    "demand_return",
                    f"{mass} 毫克已在处置点 {site} 预接收，保全要求退回"))
            else:
                conclusions.append(LocationConclusion(
                    sample_id, hold.id, "on_site", mass,
                    "freeze_location",
                    f"{mass} 毫克仍在 {site} 站内，冻结库位"))
        if pos.in_transit:
            conclusions.append(LocationConclusion(
                sample_id, hold.id, "in_transit", pos.in_transit,
                "intercept",
                f"{pos.in_transit} 毫克在途，通知拦截退回"))
        if pos.at_destination:
            conclusions.append(LocationConclusion(
                sample_id, hold.id, "pre_received", pos.at_destination,
                "demand_return",
                f"{pos.at_destination} 毫克在处置点待处置，要求退回"))
        if pos.destroyed:
            pre = [r for r in hold_rules.pre_hold_destructions(bundle, ledger, hold)
                   if r.sample == sample_id]
            pre_mass = sum(r.mass for r in pre)
            post_mass = pos.destroyed - pre_mass
            if pre_mass:
                conclusions.append(LocationConclusion(
                    sample_id, hold.id, "destroyed", pre_mass,
                    "write_off",
                    (f"{pre_mass} 毫克在保全生效前已销毁（业务时间 "
                     f"{min(r.happened for r in pre).isoformat()}），"
                     "冲减可复核量并保留处置依据")))
            if post_mass:
                conclusions.append(LocationConclusion(
                    sample_id, hold.id, "destroyed", post_mass,
                    "breach_investigation",
                    f"{post_mass} 毫克销毁发生在保全生效之后，须调查违约"))
    return conclusions


def late_destruction_reconciliation(bundle, hold, knowledge_at=None, at=None):
    """保全到达后才收到销毁回执时的冲减与复核充足性结论。"""
    ledger_known = replay(bundle, knowledge_at=knowledge_at)
    ledger_full = replay(bundle)
    pre_receipts = hold_rules.pre_hold_destructions(bundle, ledger_full, hold)

    write_offs = []
    for receipt in pre_receipts:
        ticket = bundle.tickets[receipt.ticket]
        write_offs.append({
            "receipt": receipt.id,
            "ticket": receipt.ticket,
            "sample": receipt.sample,
            "destroyed_mass": receipt.mass,
            "destroyed_at": receipt.happened.isoformat(),
            "arrived_at": receipt.arrived.isoformat(),
            "hold_effective": hold.effective.isoformat(),
            "pre_hold": receipt.happened < hold.effective,
            # 处置依据：申请时间、允许范围（整张单的申请质量与去向）
            "basis": {
                "requested_at": ticket.requested.isoformat(),
                "allowed_mass": ticket.mass,
                "destination": ticket.destination,
                "station": ticket.station,
            },
        })

    # 冲减后按当前仍有效的报告重新评估复核充足性（保全生效前的历史
    # 报告只保留在处置依据与回执链里，不作为当前复核要求）
    if at is None:
        at = max((r.valid_until for r in bundle.reports.values()),
                 default=hold.reported)
    satisfaction = claim_satisfaction(bundle, ledger_full, at)
    affected = []
    covered = hold_rules.covered_samples(bundle, hold)
    for row in satisfaction:
        if row["claim_sample"] in covered or any(
                sid in covered for sid in row["family"]):
            affected.append(row)

    return {
        "hold": hold.id,
        "write_offs": write_offs,
        "total_written_off": sum(w["destroyed_mass"] for w in write_offs),
        "review_after_write_off": affected,
        "review_still_sufficient": all(r["satisfied"] for r in affected)
        if affected else None,
        "known_at": knowledge_at.isoformat() if knowledge_at else None,
    }


def confirmations_overview(bundle, knowledge_at=None):
    """记录不全时列出还有谁待确认；缺口不触发第二张处置单。"""
    ledger = replay(bundle, knowledge_at=knowledge_at)
    rows = []
    for flow in sorted(ledger.flows.values(), key=lambda f: f.ticket_id):
        if not flow.open and not flow.confirmations_due():
            continue
        rows.append({
            "ticket": flow.ticket_id,
            "sample": flow.sample_id,
            "stage": flow.stage,
            "confirmations_due": flow.confirmations_due(),
            "gaps": ledger.position(flow.sample_id).gaps,
            "inconsistencies": flow.inconsistencies,
            "duplicate_ticket_blocked": flow.open,
        })
    return rows
