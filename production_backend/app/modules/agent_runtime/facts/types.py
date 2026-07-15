from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FactInput:
    fact_key: str
    value: Any
    certainty: str = "explicit"
    evidence: str = ""
    subject: str = ""


@dataclass(frozen=True)
class FactApplyResult:
    applied_count: int
    rejected_count: int
