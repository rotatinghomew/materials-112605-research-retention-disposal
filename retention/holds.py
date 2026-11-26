"""法务保全：范围解析、释放与跨站执行。

两类保全范围不同，不能互相扩大：
- sample：仅针对凭条列明的一组实物，以及从这些实物划出的等分；
- report：针对报告争议生效时仍存在的特定实物，即报告 claims 列明的
  样片及其拆分件，不扩大到同 origin 的其他部门留样。

释放（scope=hold）只解除它引用的那条保全。误绑后撤回不影响其他
独立保全；被释放与仍然有效由生效时间决定，而不是到达时间。
"""
from .model import HoldScope, HoldState, ReceiptState


def descendants(bundle, sample_id):
    """返回样品本身及全部拆分后代（身份不会因拆出新标签而消灭）。"""
    result = {sample_id}
    frontier = [sample_id]
    while frontier:
        parent = frontier.pop()
        for sample in bundle.samples.values():
            if sample.parent == parent and sample.id not in result:
                result.add(sample.id)
                frontier.append(sample.id)
    return result


def _released_holds(bundle, at):
    """到 at 时刻已生效的释放指令所解除的保全 id。"""
    released = set()
    for hold in bundle.holds.values():
        if hold.scope is HoldScope.HOLD and hold.state is HoldState.RELEASED \
                and hold.effective <= at:
            released.update(hold.references)
    return released


def open_holds(bundle, at):
    """at 时刻仍须履行的保全（按业务生效时间，不看到达时间）。"""
    released = _released_holds(bundle, at)
    return [
        hold for hold in bundle.holds.values()
        if hold.scope is not HoldScope.HOLD
        and hold.state is HoldState.OPEN
        and hold.effective <= at
        and hold.id not in released
    ]


def covered_samples(bundle, hold):
    """保全凭条覆盖的身份集合（质量份额在执行视图里动态计算）。"""
    covered = set()
    if hold.scope is HoldScope.SAMPLE:
        for ref in hold.references:
            covered.update(descendants(bundle, ref))
    elif hold.scope is HoldScope.REPORT:
        for ref in hold.references:
            report = bundle.reports[ref]
            for claim in report.claims:
                covered.update(descendants(bundle, claim.sample))
    return covered


def hold_coverage(bundle, at):
    """返回 {样品 id: [覆盖它的有效保全]}，用于处置前的冲突检查。"""
    coverage = {}
    for hold in open_holds(bundle, at):
        for sample_id in covered_samples(bundle, hold):
            coverage.setdefault(sample_id, []).append(hold)
    return coverage


def is_held(bundle, sample_id, at):
    return sample_id in hold_coverage(bundle, at)


def _sample_locations(bundle, ledger, sample_id):
    pos = ledger.position(sample_id)
    sites = dict(pos.on_hand)
    if pos.in_transit:
        sites["__transit__"] = sites.get("__transit__", 0) + pos.in_transit
    if pos.at_destination:
        sites["__destination__"] = (
            sites.get("__destination__", 0) + pos.at_destination)
    return sites


def recovery_actions(bundle, ledger, hold):
    """保全生效后，按实物所在位置给出追回动作。

    站内：冻结库位、撤回尚未出库的处置单；
    在途：通知承运/处置点拦截，待预接收后退回；
    处置点在手：要求退回，不得销毁；
    已销毁：若销毁业务时间早于保全生效则为生效前既成事实，
    只登记冲减，不生成追回动作。
    """
    actions = []
    covered = covered_samples(bundle, hold)
    for sample_id in sorted(covered):
        pos = ledger.position(sample_id)
        for site, mass in sorted(pos.on_hand.items()):
            if mass <= 0:
                continue
            if bundle.sites[site].capability.value == "destroy":
                actions.append({"sample": sample_id, "where": site,
                                "mass": mass, "action": "demand_return",
                                "reason": "实物在处置点，保全要求退回"})
            else:
                actions.append({"sample": sample_id, "where": site,
                                "mass": mass, "action": "freeze_location",
                                "reason": "实物在站内，冻结库位"})
        if pos.in_transit:
            actions.append({"sample": sample_id, "where": "in_transit",
                            "mass": pos.in_transit, "action": "intercept",
                            "reason": "实物在途，拦截并退回原站"})
        if pos.at_destination:
            actions.append({"sample": sample_id,
                            "where": "at_destination",
                            "mass": pos.at_destination,
                            "action": "demand_return",
                            "reason": "处置点已预接收，保全要求退回"})
        if pos.destroyed:
            actions.append({"sample": sample_id, "where": "destroyed",
                            "mass": pos.destroyed,
                            "action": "note_pre_hold_destruction",
                            "reason": "已销毁份额按业务时间核对是否早于保全生效"})
    # 尚未出库的处置单要撤回，避免保全冲突下继续签发
    for flow in ledger.flows.values():
        if flow.sample_id in covered and flow.stage == "drafted":
            actions.append({"sample": flow.sample_id, "where": flow.station,
                            "mass": flow.requested_mass,
                            "action": "withdraw_ticket",
                            "ticket": flow.ticket_id,
                            "reason": "处置单尚未出库，保全生效须撤回"})
    return actions


def site_hold_tasks(bundle, ledger, site_id, at):
    """保管点按自己的实物交接清单办理保全：只返回与本站有关的任务。

    同一样片的在站冻结与单据撤回合并为一条，动作列入 actions。
    """
    grouped = {}
    for hold in open_holds(bundle, at):
        covered = covered_samples(bundle, hold)
        for sample_id in sorted(covered):
            pos = ledger.position(sample_id)
            mass_here = pos.on_hand_at(site_id)
            key = (hold.id, sample_id)
            task = grouped.setdefault(key, {
                "hold": hold.id, "sample": sample_id, "mass": 0,
                "on_site": mass_here > 0, "actions": [],
                "tickets": []})
            if mass_here:
                task["mass"] = mass_here
                task["actions"].append("freeze_location")
            for flow in ledger.open_tickets_for(sample_id):
                if flow.station != site_id:
                    continue
                if flow.in_transit_mass:
                    task["mass"] = max(task["mass"], flow.in_transit_mass)
                    task["on_site"] = False
                    task["actions"].append("intercept")
                    task["tickets"].append(flow.ticket_id)
                if flow.stage == "drafted":
                    task["actions"].append("withdraw_ticket")
                    task["tickets"].append(flow.ticket_id)
    return [task for task in grouped.values() if task["actions"]]


def pre_hold_destructions(bundle, ledger, hold):
    """保全覆盖身份中，销毁业务时间早于保全生效的回执（既成事实）。"""
    covered = covered_samples(bundle, hold)
    result = []
    for receipt in bundle.receipts.values():
        if receipt.state is ReceiptState.DESTROYED \
                and receipt.sample in covered \
                and receipt.happened < hold.effective:
            result.append(receipt)
    return sorted(result, key=lambda r: (r.happened, r.id))
