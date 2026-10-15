"""Small text helpers shared by the page layout and the site builder."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def safe_url(url: str | None) -> str | None:
    """The URL when its scheme is http or https; None otherwise (javascript:, data:, relative, malformed)."""
    if not url:
        return None
    url = url.strip()
    try:
        scheme = urlsplit(url).scheme.strip().lower()
    except ValueError:
        return None
    return url if scheme in ("http", "https") else None


LONG_TITLE = 120


def short_title(title: str, limit: int = 100) -> str:
    """A title cut at a word boundary for the headline; the full text stays on the page under a disclosure."""
    if len(title) <= LONG_TITLE:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return f"{cut}…"


_ABSENCE = re.compile(r"no further|gives no|provides no|no other details?|no details|only the meeting agenda"
                      r"|does not (?:name|give|say|state|specify)", re.I)


def trim_summary(summary: str) -> str:
    """Drop sentences that only say what the source leaves out. Older summaries carry them; the prompt now forbids them."""
    pieces = re.split(r"(?<=[.;])\s+", summary.strip())
    keep = [p for p in pieces if not _ABSENCE.search(p)]
    if not keep or len(keep) == len(pieces):
        return summary
    keep = [p[:-1] + "." if p.endswith(";") else p for p in keep]
    return " ".join(p[0].upper() + p[1:] for p in keep)  # a clause that followed a dropped "...;" starts a sentence now
