"""领域记录：站点、实物、报告、保全凭条、处置单、交接/销毁回执。

字段命名与 docs/exchange.md 的交换资料一致。记录本身不可变，业务变更
（拆分、更正、保全、回执）通过服务追加新记录表达，已确认的身份不被覆盖。
"""
from dataclasses import dataclass, field
from enum import Enum

from .timeparse import parse_time


class DomainError(ValueError):
    """业务规则被违反。"""


class SiteCapability(str, Enum):
    RETAIN = "retain"
    DESTROY = "destroy"


class ClaimMode(str, Enum):
    EXACT = "exact"      # 只认可报告所用那块（含其剩余部分与拆分件）
    ORIGIN = "origin"    # 允许同一取样来源的任意留样替代


class HoldScope(str, Enum):
    SAMPLE = "sample"    # 针对凭条列明的一组实物及其等分
    REPORT = "report"    # 针对报告争议时仍存在的特定实物
    HOLD = "hold"        # 对另一条保全的释放指令


class HoldState(str, Enum):
    OPEN = "open"
    RELEASED = "released"


class ReceiptState(str, Enum):
    DISPATCHED = "dispatched"  # 保管点出库（送达的起点，不是销毁）
    ACCEPTED = "accepted"      # 处置点预接收
    DESTROYED = "destroyed"    # 处置点确认销毁
    RETURNED = "returned"      # 处置点退回，回到原站


@dataclass(frozen=True)
class Site:
    id: str
    org: str
    capability: SiteCapability


@dataclass(frozen=True)
class Sample:
    id: str
    origin: str
    parent: str | None
    mass: int
    initial_site: str
    earliest: object          # 最早出库时间
    split_at: object | None = None  # 拆分发生时间；初始资料为 None

    def label(self):
        return self.id


@dataclass(frozen=True)
class Claim:
    """报告对留样的要求：[样片, 认可方式, 复核最低质量(毫克)]。"""
    sample: str
    mode: ClaimMode
    min_mass: int


@dataclass(frozen=True)
class Report:
    id: str
    archive: str
    owner: str
    version: int
    claims: tuple[Claim, ...]
    valid_from: object
    valid_until: object
    replaces: str | None = None


@dataclass(frozen=True)
class Hold:
    id: str
    legal: str
    scope: HoldScope
    references: tuple[str, ...]
    state: HoldState
    effective: object
    reported: object


@dataclass(frozen=True)
class Ticket:
    """库管拟办的处置/交接申请，mass 为本张单待确认的质量。"""
    id: str
    sample: str
    station: str
    destination: str
    mass: int
    requested: object


@dataclass(frozen=True)
class Receipt:
    id: str
    ticket: str
    sample: str
    actor: str
    state: ReceiptState
    mass: int
    happened: object       # 业务发生时间
    arrived: object        # 对接方可以提供该回执的时间


@dataclass(frozen=True)
class Bundle:
    sites: dict[str, Site] = field(default_factory=dict)
    samples: dict[str, Sample] = field(default_factory=dict)
    reports: dict[str, Report] = field(default_factory=dict)
    holds: dict[str, Hold] = field(default_factory=dict)
    tickets: dict[str, Ticket] = field(default_factory=dict)
    receipts: dict[str, Receipt] = field(default_factory=dict)


def _require_unique(rows, key, collection):
    seen = set()
    for row in rows:
        rid = row[key]
        if not isinstance(rid, str) or not rid:
            raise DomainError(f"{collection} 记录 id 必须是非空字符串")
        if rid in seen:
            raise DomainError(f"{collection} 记录 id 重复: {rid}")
        seen.add(rid)


def load_bundle(data: dict) -> Bundle:
    """把交换 JSON 读成领域对象，并做跨集合引用与字段校验。"""
    for name in ("sites", "samples", "reports", "holds", "tickets", "receipts"):
        if name not in data or not isinstance(data[name], list):
            raise DomainError(f"交换资料缺少集合 {name}")
    for name in ("sites", "samples", "reports", "holds", "tickets", "receipts"):
        _require_unique(data[name], "id", name)

    sites = {}
    for row in data["sites"]:
        sites[row["id"]] = Site(
            id=row["id"], org=row["org"],
            capability=SiteCapability(row["capability"]),
        )

    samples = {}
    for row in data["samples"]:
        parent = row["parent"]
        if parent is not None and parent not in samples and not any(
            s["id"] == parent for s in data["samples"]
        ):
            raise DomainError(f"样品 {row['id']} 的母样 {parent} 不存在")
        samples[row["id"]] = Sample(
            id=row["id"], origin=row["origin"], parent=parent,
            mass=int(row["mass"]), initial_site=row["initial_site"],
            earliest=parse_time(row["earliest"]), split_at=None,
        )
    for sample in samples.values():
        if sample.initial_site not in sites:
            raise DomainError(f"样品 {sample.id} 的初始站点 {sample.initial_site} 不存在")
        if sample.parent is not None and sample.parent not in samples:
            raise DomainError(f"样品 {sample.id} 的母样 {sample.parent} 不存在")

    reports = {}
    for row in data["reports"]:
        claims = tuple(
            Claim(sample=c[0], mode=ClaimMode(c[1]), min_mass=int(c[2]))
            for c in row["claims"]
        )
        for claim in claims:
            if claim.sample not in samples:
                raise DomainError(f"报告 {row['id']} 引用了不存在的样品 {claim.sample}")
            if claim.min_mass <= 0:
                raise DomainError(f"报告 {row['id']} 的复核量必须为正")
        report = Report(
            id=row["id"], archive=row["archive"], owner=row["owner"],
            version=int(row["version"]), claims=claims,
            valid_from=parse_time(row["from"]),
            valid_until=parse_time(row["until"]),
            replaces=row.get("replaces"),
        )
        if report.valid_until <= report.valid_from:
            raise DomainError(f"报告 {report.id} 的有效期起止不合法")
        reports[report.id] = report
    for report in reports.values():
        if report.replaces is not None and report.replaces not in reports:
            raise DomainError(f"报告 {report.id} 更正的 {report.replaces} 不存在")
        if report.replaces == report.id:
            raise DomainError(f"报告 {report.id} 不能替换自身")

    holds = {}
    for row in data["holds"]:
        hold = Hold(
            id=row["id"], legal=row["legal"], scope=HoldScope(row["scope"]),
            references=tuple(row["references"]),
            state=HoldState(row["state"]),
            effective=parse_time(row["effective"]),
            reported=parse_time(row["reported"]),
        )
        if hold.effective > hold.reported:
            raise DomainError(f"保全 {hold.id} 的 reported 早于 effective")
        holds[hold.id] = hold
    for hold in holds.values():
        for ref in hold.references:
            if hold.scope is HoldScope.REPORT and ref not in reports:
                raise DomainError(f"保全 {hold.id} 引用的报告 {ref} 不存在")
            if hold.scope is HoldScope.SAMPLE and ref not in samples:
                raise DomainError(f"保全 {hold.id} 引用的样品 {ref} 不存在")
            if hold.scope is HoldScope.HOLD and ref not in holds:
                raise DomainError(f"保全 {hold.id} 引用的保全 {ref} 不存在")

    tickets = {}
    for row in data["tickets"]:
        ticket = Ticket(
            id=row["id"], sample=row["sample"], station=row["station"],
            destination=row["destination"], mass=int(row["mass"]),
            requested=parse_time(row["requested"]),
        )
        if ticket.sample not in samples:
            raise DomainError(f"处置单 {ticket.id} 的样品 {ticket.sample} 不存在")
        if ticket.station not in sites or ticket.destination not in sites:
            raise DomainError(f"处置单 {ticket.id} 的站点不存在")
        if ticket.mass <= 0:
            raise DomainError(f"处置单 {ticket.id} 的质量必须为正")
        tickets[ticket.id] = ticket

    receipts = {}
    for row in data["receipts"]:
        receipt = Receipt(
            id=row["id"], ticket=row["ticket"], sample=row["sample"],
            actor=row["actor"], state=ReceiptState(row["state"]),
            mass=int(row["mass"]),
            happened=parse_time(row["happened"]),
            arrived=parse_time(row["arrived"]),
        )
        if receipt.happened > receipt.arrived:
            raise DomainError(f"回执 {receipt.id} 的业务时间晚于到达时间")
        if receipt.actor not in sites:
            raise DomainError(f"回执 {receipt.id} 的经手站点 {receipt.actor} 不存在")
        if receipt.ticket not in tickets:
            raise DomainError(f"回执 {receipt.id} 的处置单 {receipt.ticket} 不存在")
        if tickets[receipt.ticket].sample != receipt.sample:
            raise DomainError(f"回执 {receipt.id} 的样品与处置单不一致")
        if receipt.mass <= 0:
            raise DomainError(f"回执 {receipt.id} 的质量必须为正")
        receipts[receipt.id] = receipt

    return Bundle(sites=sites, samples=samples, reports=reports,
                  holds=holds, tickets=tickets, receipts=receipts)


def to_jsonable(value):
    """领域对象转回交换 JSON 兼容结构。"""
    if hasattr(value, "value") and not hasattr(value, "__dataclass_fields__"):
        return value.value
    raise TypeError(type(value))
