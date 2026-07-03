from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProductAsset:
    id: str
    label: str
    domain: str
    content_type: str
    size_bytes: int
    path: Path
