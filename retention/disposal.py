"""处置申请与可处置范围核算。

冲突处理原则：
1. 到期记录与保全冲突时按保全办理——被有效保全覆盖的在站量一律冻结；
2. 报告更正使某块样片不再被引用时，仍要检查其他有效报告的 exact 引用，
   以及 origin 同源组是否还有足够替代样；
3. 可处置量具体到实际在站质量和去向；已开出未结清的处置单先占用额度，
   不允许就同一实物再签第二张处置单；
4. 出库送达回执与销毁回执是不同事实，许可范围按在站实物而非账面总数。
"""
from .model import DomainError, SiteCapability
from .ledger import replay
from . import holds as hold_rules
from .reports import required_mass_by_sample


def _reserved_by_open_tickets(ledger, sample_id):
    return sum(flow.requested_mass - flow.destroyed - flow.returned
               for flow in ledger.open_tickets_for(sample_id))


def disposal_options(bundle, at, station_id=None, knowledge_at=None):
    """逐站逐实物给出允许处置的质量与原因。"""
    ledger = replay(bundle, knowledge_at=knowledge_at)
    coverage = hold_rules.hold_coverage(bundle, at)
    exact_need, origin_need = required_mass_by_sample(bundle, at)

    # origin 组的现有总量与在站分布，用于判断替代样是否够
    origin_totals = {}
    for sample in bundle.samples.values():
        origin_totals.setdefault(sample.origin, 0)
        origin_totals[sample.origin] += ledger.position(sample.id).existing_mass

    options = []
    for sample_id, sample in bundle.samples.items():
        pos = ledger.position(sample_id)
        for site, on_hand in sorted(pos.on_hand.items()):
            if station_id is not None and site != station_id:
                continue
            site_obj = bundle.sites[site]
            if site_obj.capability is SiteCapability.DESTROY:
                # 处置点在手实物不属于可再开处置单的库位
                continue
            blocked = []
            allowed = on_hand

            held_mass = 0
            if sample_id in coverage and on_hand:
                held_mass = on_hand  # 保全按身份覆盖；在站部分全部冻结
                allowed -= held_mass
                blocked.append({
                    "reason": "hold",
                    "holds": [h.id for h in coverage[sample_id]],
                    "mass": held_mass,
                })

            exact_reserve = min(on_hand, exact_need.get(sample_id, 0))
            if exact_reserve:
                allowed -= exact_reserve
                blocked.append({"reason": "exact_claim",
                                "mass": exact_reserve})

            origin_reserve = 0
            if sample.origin in origin_need:
                # 同源组的复核要求可由组内任意成员满足。本件最多可销毁到
                # “组内其余成员 + 本件剩余”仍够 group_need 为止。
                group_need = origin_need[sample.origin]
                group_existing = origin_totals[sample.origin]
                peers = group_existing - pos.existing_mass
                # 本件至少要替整组保留的份额（其他成员顶上后的缺口）
                origin_reserve = min(on_hand, max(0, group_need - peers))
                origin_reserve = min(origin_reserve, max(0, allowed))
                if origin_reserve:
                    allowed -= origin_reserve
                    blocked.append({"reason": "origin_claim_reserve",
                                    "mass": origin_reserve,
                                    "origin": sample.origin})

            reserved = min(max(0, allowed),
                           _reserved_by_open_tickets(ledger, sample_id))
            if reserved:
                allowed -= reserved
                blocked.append({"reason": "open_ticket", "mass": reserved})

            earliest_ok = at >= sample.earliest
            if not earliest_ok and allowed > 0:
                blocked.append({"reason": "before_earliest",
                                "mass": allowed,
                                "earliest": sample.earliest.isoformat()})
                allowed = 0

            options.append({
                "sample": sample_id,
                "site": site,
                "on_hand_mass": on_hand,
                "allowed_mass": max(0, allowed),
                "blocked": blocked,
                "earliest_ok": earliest_ok,
                "held": sample_id in coverage,
            })
    return options


def assert_ticket_allowed(bundle, ticket, knowledge_at=None):
    """库管提交真实处置申请前的完整校验，失败抛 DomainError。"""
    at = ticket.requested
    if ticket.sample not in bundle.samples:
        raise DomainError(f"样品 {ticket.sample} 不存在")
    if ticket.station not in bundle.sites:
        raise DomainError(f"站点 {ticket.station} 不存在")
    if ticket.destination not in bundle.sites:
        raise DomainError(f"去向 {ticket.destination} 不存在")
    if bundle.sites[ticket.station].capability is not SiteCapability.RETAIN:
        raise DomainError("只有保管点可以发起处置/交接")
    if ticket.mass <= 0:
        raise DomainError("处置质量必须为正")

    sample = bundle.samples[ticket.sample]
    if at < sample.earliest:
        raise DomainError(
            f"{sample.id} 最早 {sample.earliest.isoformat()} 才可出库，"
            f"申请时间 {at.isoformat()} 过早")

    ledger = replay(bundle, knowledge_at=knowledge_at)
    pos = ledger.position(ticket.sample)
    on_hand = pos.on_hand_at(ticket.station)
    if on_hand < ticket.mass:
        raise DomainError(
            f"{ticket.sample} 在 {ticket.station} 实际在站 {on_hand} 毫克，"
            f"不足以申请 {ticket.mass} 毫克")

    if hold_rules.is_held(bundle, ticket.sample, at):
        raise DomainError(
            f"{ticket.sample} 处于有效保全范围内，按保全办理，不得开出处置单")

    # 未结清处置单占用：同一实物不签第二张单
    waiting = _reserved_by_open_tickets(ledger, ticket.sample)
    if waiting > 0:
        raise DomainError(
            f"{ticket.sample} 已有未结清处置单占用 {waiting} 毫克，"
            "须先完成或撤回原单，不得重复签单")

    # 报告复核需求（含替代样判断）
    exact_need, origin_need = required_mass_by_sample(bundle, at)
    if ticket.sample in exact_need:
        remain = pos.existing_mass - ticket.mass
        if remain < exact_need[ticket.sample]:
            raise DomainError(
                f"{ticket.sample} 是有效报告的 exact 留样，销毁后复核量 "
                f"{remain} 低于要求 {exact_need[ticket.sample]}")
    if sample.origin in origin_need:
        group_existing = sum(
            ledger.position(s.id).existing_mass
            for s in bundle.samples.values() if s.origin == sample.origin)
        if group_existing - ticket.mass < origin_need[sample.origin]:
            raise DomainError(
                f"同源 {sample.origin} 替代后复核量 "
                f"{group_existing - ticket.mass} 低于要求 "
                f"{origin_need[sample.origin]}")

    # 去向必须与权能匹配：销毁只能送往 destroy 点；retain 点之间是交接
    destination = bundle.sites[ticket.destination]
    if destination.capability is SiteCapability.DESTROY:
        return "destroy"
    return "transfer"


def station_worklist(bundle, station_id, at, knowledge_at=None):
    """保管点自己的实物交接清单：可处置项 + 待确认回执 + 保全任务。"""
    ledger = replay(bundle, knowledge_at=knowledge_at)
    options = [o for o in disposal_options(
        bundle, at, station_id=station_id, knowledge_at=knowledge_at)]
    pending_tickets = []
    for flow in ledger.flows.values():
        if flow.station != station_id or not flow.open:
            continue
        pending_tickets.append({
            "ticket": flow.ticket_id,
            "sample": flow.sample_id,
            "stage": flow.stage,
            "requested_mass": flow.requested_mass,
            "dispatched": flow.dispatched,
            "accepted": flow.accepted,
            "destroyed": flow.destroyed,
            "returned": flow.returned,
            "confirmations_due": flow.confirmations_due(),
            "inconsistencies": flow.inconsistencies,
        })
    hold_tasks = hold_rules.site_hold_tasks(bundle, ledger, station_id, at)
    return {
        "site": station_id,
        "at": at.isoformat(),
        "disposal_options": options,
        "pending_tickets": pending_tickets,
        "hold_tasks": hold_tasks,
    }
