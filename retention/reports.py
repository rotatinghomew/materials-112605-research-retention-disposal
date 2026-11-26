"""报告登记与复核量核算。

一份报告可以由多块留样支撑；同一块样片也可以同时支撑多份报告，
因此同一条 claim 的复核要求按“满足最大值”而不是累加计算。

- exact：只认可报告所用那块的剩余部分及其拆分件；
- origin：认可同一取样来源的任意留样互相替代。

更正报告（replaces）从自身 valid_from 起接管被替换报告，旧报告的
历史引用和既有保全关系仍可追溯，拆分新标签不会抹掉旧身份。
"""
from .model import ClaimMode
from .ledger import replay
from . import holds as hold_rules


def active_reports(bundle, at):
    """at 时刻仍有效的报告：在有效期内，且未被已生效的更正报告接管。"""
    superseded = set()
    for report in bundle.reports.values():
        if report.replaces and report.valid_from <= at:
            superseded.add(report.replaces)
    return [
        r for r in bundle.reports.values()
        if r.valid_from <= at < r.valid_until and r.id not in superseded
    ]


def _claim_family(bundle, claim):
    """一条 claim 认可的全部身份。"""
    if claim.mode is ClaimMode.ORIGIN:
        origin = bundle.samples[claim.sample].origin
        return {sid for sid, s in bundle.samples.items() if s.origin == origin}
    return hold_rules.descendants(bundle, claim.sample)


def _existing_mass(ledger, sample_ids):
    return sum(ledger.position(sid).existing_mass for sid in sample_ids)


def _on_hand_mass(ledger, sample_ids):
    return {sid: ledger.position(sid).existing_mass for sid in sample_ids}


def claim_satisfaction(bundle, ledger, at):
    """逐条 claim 给出可复核量与是否满足 min。

    origin 类 claim 合并同源可用量；exact 只看指定身份及其拆分件。
    """
    result = []
    for report in active_reports(bundle, at):
        for claim in report.claims:
            family = _claim_family(bundle, claim)
            available = _existing_mass(ledger, family)
            result.append({
                "report": report.id,
                "claim_sample": claim.sample,
                "mode": claim.mode.value,
                "min_mass": claim.min_mass,
                "available_mass": available,
                "satisfied": available >= claim.min_mass,
                "family": sorted(family),
                "by_sample": _on_hand_mass(ledger, family),
            })
    return result


def required_mass_by_sample(bundle, at):
    """为满足当时所有有效报告，每个身份至少要保留的质量。

    同一样片支撑多份报告时取最大需求；origin 需求落到来源分组，
    允许组内任意样片替代，因此只返回分组级保留量。
    """
    exact_need = {}
    origin_need = {}   # (origin) -> max min
    for report in active_reports(bundle, at):
        for claim in report.claims:
            if claim.mode is ClaimMode.EXACT:
                exact_need[claim.sample] = max(
                    exact_need.get(claim.sample, 0), claim.min_mass)
            else:
                origin = bundle.samples[claim.sample].origin
                origin_need[origin] = max(
                    origin_need.get(origin, 0), claim.min_mass)
    return exact_need, origin_need


def archive_view(bundle, at=None, knowledge_at=None):
    """档案员按报告查看目前可用复核样与保全执行进度。"""
    if at is None:
        at = max((r.valid_until for r in bundle.reports.values()),
                 default=None)
    ledger = replay(bundle, knowledge_at=knowledge_at)
    satisfaction = claim_satisfaction(bundle, ledger, at)
    report_rows = []
    for report in active_reports(bundle, at):
        row_claims = [c for c in satisfaction if c["report"] == report.id]
        holding = []
        for hold in hold_rules.open_holds(bundle, at):
            if hold.scope.value == "report" and report.id in hold.references:
                holding.append({
                    "hold": hold.id,
                    "effective": hold.effective.isoformat(),
                    "covered": sorted(hold_rules.covered_samples(bundle, hold)),
                    "actions": hold_rules.recovery_actions(bundle, ledger, hold),
                })
        report_rows.append({
            "report": report.id,
            "version": report.version,
            "replaces": report.replaces,
            "owner": report.owner,
            "satisfied": all(c["satisfied"] for c in row_claims),
            "claims": row_claims,
            "holds": holding,
        })
    return {"at": at.isoformat(), "reports": report_rows}
