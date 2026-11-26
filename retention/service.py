"""应用服务：档案员、经手人、库管、法务各角色的命令入口。

所有写操作都返回新记录并做领域校验；状态保存在内存 Bundle 中，
可通过 export_json 落盘。事件时间与到达时间分开记录，到达不代替业务时间。
"""
import json
from dataclasses import replace

from .model import (
    Bundle, Claim, ClaimMode, DomainError, Hold, HoldScope, HoldState,
    Receipt, ReceiptState, Report, Sample, Site, SiteCapability, Ticket,
    load_bundle, parse_time,
)
from .ledger import replay
from .disposal import assert_ticket_allowed


class RetentionService:
    def __init__(self, bundle: Bundle | None = None):
        self.bundle = bundle or Bundle()

    # ---------- 装载与导出 ----------
    @classmethod
    def from_json(cls, path_or_text):
        if hasattr(path_or_text, "read_text"):
            data = json.loads(path_or_text.read_text(encoding="utf-8"))
        elif isinstance(path_or_text, str) and path_or_text.strip().startswith("{"):
            data = json.loads(path_or_text)
        else:
            with open(path_or_text, "r", encoding="utf-8") as fh:
                data = json.loads(fh)
        return cls(load_bundle(data))

    def export_json(self) -> dict:
        return {
            "sites": [{"id": s.id, "org": s.org, "capability": s.capability.value}
                      for s in self.bundle.sites.values()],
            "samples": [{
                "id": s.id, "origin": s.origin, "parent": s.parent,
                "mass": s.mass, "initial_site": s.initial_site,
                "earliest": s.earliest.isoformat(),
                **({"split_at": s.split_at.isoformat()} if s.split_at else {}),
            } for s in self.bundle.samples.values()],
            "reports": [{
                "id": r.id, "archive": r.archive, "owner": r.owner,
                "version": r.version,
                "claims": [[c.sample, c.mode.value, c.min_mass] for c in r.claims],
                "from": r.valid_from.isoformat(),
                "until": r.valid_until.isoformat(),
                "replaces": r.replaces,
            } for r in self.bundle.reports.values()],
            "holds": [{
                "id": h.id, "legal": h.legal, "scope": h.scope.value,
                "references": list(h.references), "state": h.state.value,
                "effective": h.effective.isoformat(),
                "reported": h.reported.isoformat(),
            } for h in self.bundle.holds.values()],
            "tickets": [{
                "id": t.id, "sample": t.sample, "station": t.station,
                "destination": t.destination, "mass": t.mass,
                "requested": t.requested.isoformat(),
            } for t in self.bundle.tickets.values()],
            "receipts": [{
                "id": r.id, "ticket": r.ticket, "sample": r.sample,
                "actor": r.actor, "state": r.state.value, "mass": r.mass,
                "happened": r.happened.isoformat(),
                "arrived": r.arrived.isoformat(),
            } for r in self.bundle.receipts.values()],
        }

    def _check_id(self, collection, rid):
        if not isinstance(rid, str) or not rid:
            raise DomainError("记录 id 必须是非空字符串")
        if rid in collection:
            raise DomainError(f"记录 id 已存在: {rid}")

    # ---------- 档案员：站点与报告 ----------
    def register_site(self, site_id, org, capability):
        self._check_id(self.bundle.sites, site_id)
        cap = SiteCapability(capability)
        site = Site(id=site_id, org=org, capability=cap)
        self.bundle.sites[site_id] = site
        return site

    def register_report(self, report_id, archive, owner, version, claims,
                        valid_from, valid_until, replaces=None):
        self._check_id(self.bundle.reports, report_id)
        valid_from = parse_time(valid_from) if isinstance(valid_from, str) else valid_from
        valid_until = parse_time(valid_until) if isinstance(valid_until, str) else valid_until
        if valid_until <= valid_from:
            raise DomainError("报告有效期起止不合法")
        parsed_claims = []
        for sample_id, mode, min_mass in claims:
            if sample_id not in self.bundle.samples:
                raise DomainError(f"报告引用的样品 {sample_id} 不存在")
            if min_mass <= 0:
                raise DomainError("复核量必须为正")
            parsed_claims.append(
                Claim(sample_id, ClaimMode(mode), int(min_mass)))
        if replaces is not None:
            if replaces not in self.bundle.reports:
                raise DomainError(f"被更正报告 {replaces} 不存在")
            if replaces == report_id:
                raise DomainError("报告不能替换自身")
        report = Report(
            id=report_id, archive=archive, owner=owner, version=int(version),
            claims=tuple(parsed_claims), valid_from=valid_from,
            valid_until=valid_until, replaces=replaces)
        self.bundle.reports[report_id] = report
        return report

    # ---------- 经手人：样品拆分 ----------
    def split_sample(self, new_id, parent_id, mass, at,
                     initial_site=None, earliest=None):
        """从母样划出等分：新标签新身份，母样可用量扣除，保全关系延续。"""
        self._check_id(self.bundle.samples, new_id)
        if parent_id not in self.bundle.samples:
            raise DomainError(f"母样 {parent_id} 不存在")
        parent = self.bundle.samples[parent_id]
        at = parse_time(at) if isinstance(at, str) else at
        mass = int(mass)
        if mass <= 0:
            raise DomainError("拆分质量必须为正")
        ledger = replay(self.bundle)
        pos = ledger.position(parent_id)
        if pos.existing_mass < mass:
            raise DomainError(
                f"母样 {parent_id} 现有 {pos.existing_mass} 毫克，"
                f"不足以划出 {mass} 毫克")
        site = initial_site or parent.initial_site
        if site not in self.bundle.sites:
            raise DomainError(f"站点 {site} 不存在")
        child = Sample(
            id=new_id, origin=parent.origin, parent=parent_id, mass=mass,
            initial_site=site,
            earliest=parse_time(earliest) if isinstance(earliest, str)
            else (earliest or parent.earliest),
            split_at=at)
        # 母样消耗份额：母样质量账面扣减（拆分件 ID 不可复用）
        reduced = replace(parent, mass=parent.mass - mass)
        self.bundle.samples[parent_id] = reduced
        self.bundle.samples[new_id] = child
        return child

    # ---------- 法务：保全与释放 ----------
    def place_hold(self, hold_id, legal, scope, references, effective,
                   reported=None, state=HoldState.OPEN):
        self._check_id(self.bundle.holds, hold_id)
        scope = HoldScope(scope)
        effective = parse_time(effective) if isinstance(effective, str) else effective
        reported = parse_time(reported) if isinstance(reported, str) else (reported or effective)
        if effective > reported:
            raise DomainError("reported 不能早于 effective")
        refs = tuple(references)
        for ref in refs:
            if scope is HoldScope.REPORT and ref not in self.bundle.reports:
                raise DomainError(f"保全引用的报告 {ref} 不存在")
            if scope is HoldScope.SAMPLE and ref not in self.bundle.samples:
                raise DomainError(f"保全引用的样品 {ref} 不存在")
            if scope is HoldScope.HOLD and ref not in self.bundle.holds:
                raise DomainError(f"保全引用的保全 {ref} 不存在")
        hold = Hold(id=hold_id, legal=legal, scope=scope, references=refs,
                    state=HoldState(state), effective=effective, reported=reported)
        self.bundle.holds[hold_id] = hold
        return hold

    def release_hold(self, release_id, legal, target_hold_id, effective,
                     reported=None):
        """撤回/释放只作用于被引用的保全，独立保全继续履行。"""
        if target_hold_id not in self.bundle.holds:
            raise DomainError(f"被释放保全 {target_hold_id} 不存在")
        return self.place_hold(
            release_id, legal, HoldScope.HOLD, [target_hold_id],
            effective, reported, state=HoldState.RELEASED)

    # ---------- 库管：处置申请 ----------
    def submit_ticket(self, ticket_id, sample_id, station, destination,
                      mass, requested):
        self._check_id(self.bundle.tickets, ticket_id)
        requested = parse_time(requested) if isinstance(requested, str) else requested
        ticket = Ticket(id=ticket_id, sample=sample_id, station=station,
                        destination=destination, mass=int(mass),
                        requested=requested)
        kind = assert_ticket_allowed(self.bundle, ticket)
        self.bundle.tickets[ticket_id] = ticket
        return ticket, kind

    # ---------- 经手/处置点：回执 ----------
    def record_receipt(self, receipt_id, ticket_id, actor, state, mass,
                       happened, arrived=None):
        self._check_id(self.bundle.receipts, receipt_id)
        if ticket_id not in self.bundle.tickets:
            raise DomainError(f"处置单 {ticket_id} 不存在")
        ticket = self.bundle.tickets[ticket_id]
        if actor not in self.bundle.sites:
            raise DomainError(f"经手站点 {actor} 不存在")
        happened = parse_time(happened) if isinstance(happened, str) else happened
        arrived = parse_time(arrived) if isinstance(arrived, str) else (arrived or happened)
        if happened > arrived:
            raise DomainError("业务时间不能晚于到达时间")
        state = ReceiptState(state)
        mass = int(mass)
        if mass <= 0:
            raise DomainError("回执质量必须为正")
        if mass > ticket.mass:
            raise DomainError(
                f"回执质量 {mass} 超出处置单 {ticket_id} 申请量 {ticket.mass}")
        # 状态链校验：不能跳过出库直接销毁（退回后重发允许再次 dispatched）
        ledger = replay(self.bundle)
        flow = ledger.flows.get(ticket_id)
        if state is ReceiptState.ACCEPTED and (
                flow is None or flow.dispatched - flow.accepted - flow.returned < mass):
            raise DomainError("预接收回执缺少对应的在途出库份额")
        if state is ReceiptState.DESTROYED:
            if self.bundle.sites[actor].capability is not SiteCapability.DESTROY:
                raise DomainError(f"站点 {actor} 无销毁权能")
            if flow is None or flow.accepted - flow.destroyed - flow.returned < mass:
                raise DomainError("销毁回执缺少处置点已预接收的份额")
        if state is ReceiptState.RETURNED and (
                flow is None or flow.accepted - flow.destroyed - flow.returned < mass):
            raise DomainError("退回回执缺少处置点在手份额")
        receipt = Receipt(id=receipt_id, ticket=ticket_id, sample=ticket.sample,
                          actor=actor, state=state, mass=mass,
                          happened=happened, arrived=arrived)
        self.bundle.receipts[receipt_id] = receipt
        return receipt

    # ---------- 对接方同步 ----------
    def sync_receipts(self, remote_receipts):
        """把对接方回执回放结果并入本地；已存在的 id 幂等跳过。

        读到回执不代表本地做过操作，这里只登记证据，不自动补处置单。
        """
        added, skipped = [], []
        for row in remote_receipts:
            rid = row["id"]
            if rid in self.bundle.receipts:
                skipped.append(rid)
                continue
            added.append(self.record_receipt(
                rid, row["ticket"], row["actor"], row["state"], row["mass"],
                row["happened"], row["arrived"]))
        return {"added": [r.id for r in added], "skipped": skipped}
