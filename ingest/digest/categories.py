from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import CategoryHit


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    bodies: tuple[str, ...]


def keyword_match(kw: str, text: str | None) -> bool:
    """Word-bounded. Short all-caps acronyms (CHA, PILOT, ARO) match case-sensitively; everything else ignores case."""
    flags = 0 if (kw.isupper() and len(kw) <= 6) else re.IGNORECASE
    return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text or "", flags) is not None


def merge_hits(*groups: list[CategoryHit]) -> list[CategoryHit]:
    out: dict[str, CategoryHit] = {}
    for group in groups:
        for h in group:
            out.setdefault(h.id, h)
    return list(out.values())


class Categories:
    def __init__(self, path: Path):
        self.path = path
        self._raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(self._raw)
        self.items = [
            Category(c["id"], c["label"], c["description"], tuple(c.get("keywords") or ()), tuple(c.get("bodies") or ()))
            for c in data["categories"]
        ]
        self.by_id = {c.id: c for c in self.items}

    def fingerprint(self) -> str:
        return hashlib.sha256(self._raw.encode("utf-8")).hexdigest()[:12]

    def rule_hits(self, body: str | None, texts: list[str]) -> list[CategoryHit]:
        hits = []
        for c in self.items:
            if body and body in c.bodies:
                hits.append(CategoryHit(id=c.id, via="body", detail=body))
                continue
            kw = next((k for k in c.keywords if any(keyword_match(k, t) for t in texts)), None)
            if kw:
                hits.append(CategoryHit(id=c.id, via="keyword", detail=kw))
        return hits

    def prompt_block(self) -> str:
        return "\n".join(f"- {c.id}: {c.label}. {c.description}" for c in self.items)
