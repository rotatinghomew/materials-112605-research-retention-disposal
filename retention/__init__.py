"""材料留样跨站保全与处置后端。

领域规则见 docs/exchange.md 与 docs/backend.md。包内只依赖 Python 标准库。
"""
from .timeparse import parse_time
from .model import (
    Claim,
    ClaimMode,
    DomainError,
    Hold,
    HoldScope,
    HoldState,
    Receipt,
    ReceiptState,
    Report,
    Sample,
    Site,
    SiteCapability,
    Ticket,
    load_bundle,
)
from .service import RetentionService

__all__ = [
    "parse_time",
    "Claim",
    "ClaimMode",
    "DomainError",
    "Hold",
    "HoldScope",
    "HoldState",
    "Receipt",
    "ReceiptState",
    "Report",
    "Sample",
    "Site",
    "SiteCapability",
    "Ticket",
    "load_bundle",
    "RetentionService",
]
