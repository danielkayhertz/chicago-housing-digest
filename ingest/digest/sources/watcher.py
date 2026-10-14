"""Page-change watcher: new links and added text on watched pages become page_change notices."""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import FetchError, Fetcher
from ..models import Notice, SourceResult
from .base import BaseAdapter, ImplausibleResult


MIN_TEXT_CHARS = 200  # a fetched page with less visible text than this is treated as a stub
MIN_LINKS_FOR_DROP_CHECK = 10  # link-count collapse is only checked once the baseline had this many links


def _stub_reason(prev: dict | None, cur: dict) -> str | None:
    if len(cur["text"]) < MIN_TEXT_CHARS:
        return f"only {len(cur['text'])} chars of text"
    if prev is not None and len(prev["links"]) >= MIN_LINKS_FOR_DROP_CHECK and len(cur["links"]) < len(prev["links"]) / 2:
        return f"links fell from {len(prev['links'])} to {len(cur['links'])}"
    return None


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def snapshot(html: str, base: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for t in root.find_all(["script", "style", "nav", "noscript"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", line).strip() for line in root.get_text("\n").replace("\xa0", " ").splitlines()]
    links: dict[str, str] = {}
    for a in root.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href).split("#")[0]
        links.setdefault(url, re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or url)
    return {"text": "\n".join(line for line in lines if line), "links": links}


def changes(page: dict, prev: dict | None, cur: dict, now: dt.datetime, source_id: str) -> list[Notice]:
    if prev is None:
        return []
    out = [
        Notice(id=f"{source_id}:{page['id']}:{_h(url)}", source_id=source_id, body=page["body"],
               kind="page_change", title=f"New on {page['title']}: {label}", url=url, detail=label, posted_at=now)
        for url, label in cur["links"].items() if url not in prev["links"]
    ]
    if not out and cur["text"] != prev["text"]:
        added = [
            line[1:] for line in difflib.unified_diff(prev["text"].splitlines(), cur["text"].splitlines(), lineterm="", n=0)
            if line.startswith("+") and not line.startswith("+++")
        ]
        if added:
            detail = "\n".join(added)[:2000]
            out.append(Notice(id=f"{source_id}:{page['id']}:{_h(detail)}", source_id=source_id, body=page["body"],
                              kind="page_change", title=f"{page['title']} page updated", url=page["url"],
                              detail=detail, posted_at=now))
    return out


class WatcherAdapter(BaseAdapter):
    def __init__(self, cfg: dict, pages: list[dict], snap_dir: Path):
        super().__init__(cfg)
        self.pages = pages
        self.snap_dir = snap_dir

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        res = SourceResult()
        ok = 0
        for page in self.pages:
            try:
                html = fetcher.get_text(page["url"])
            except FetchError as e:
                res.warnings.append(f"watch page {page['id']} failed: {e}")
                continue
            ok += 1
            path = self.snap_dir / f"{page['id']}.json"
            try:
                prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            except (OSError, ValueError) as e:
                res.warnings.append(f"watch page {page['id']} snapshot unreadable, re-baselining: {e}")
                prev = None
            cur = snapshot(html, page["url"])
            why = _stub_reason(prev, cur)
            if why:
                res.warnings.append(f"watch page {page['id']} looks like a stub ({why}); snapshot kept, no notices")
                continue
            res.notices += changes(page, prev, cur, now, self.source_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cur, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.pages and ok == 0:
            raise ImplausibleResult("every watched page failed")
        return res
