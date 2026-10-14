"""Hand-label a sample of enriched items, then score the model against the labels."""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from .models import Digest, load_digest

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["id", "body", "date", "title", "summary", "model_relevant", "model_categories", "label_relevant", "label_categories"]


def sample(digest: Digest, n: int = 30, seed: int = 13) -> list[dict]:
    meetings = {m.id: m for m in digest.meetings}
    done = [i for i in digest.items if i.enrichment and i.enrichment.status in ("ok", "screened_out")]
    rel = [i for i in done if i.enrichment.housing_relevant]
    non = [i for i in done if not i.enrichment.housing_relevant]
    rng = random.Random(seed)
    k = min(len(rel), n // 2)
    picked = rng.sample(rel, k) + rng.sample(non, min(len(non), n - k))
    rows = []
    for i in picked:
        m = meetings.get(i.meeting_id)
        rows.append({
            "id": i.id, "body": m.body if m else "", "date": m.start.date().isoformat() if m else "",
            "title": i.title, "summary": i.enrichment.summary,
            "model_relevant": "y" if i.enrichment.housing_relevant else "n",
            "model_categories": " ".join(c.id for c in i.enrichment.categories),
            "label_relevant": "", "label_categories": "",
        })
    return rows


def write_sample(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)


def score(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [{**r, "label_relevant": (r["label_relevant"] or "").strip().lower()} for r in csv.DictReader(f)]
    rows = [r for r in rows if r["label_relevant"] in ("y", "n")]
    tp = sum(r["model_relevant"] == "y" and r["label_relevant"] == "y" for r in rows)
    fp = sum(r["model_relevant"] == "y" and r["label_relevant"] == "n" for r in rows)
    fn = sum(r["model_relevant"] == "n" and r["label_relevant"] == "y" for r in rows)
    cats: dict[str, dict[str, int]] = {}
    for r in rows:
        model, label = set(r["model_categories"].split()), set(r["label_categories"].split())
        for c in model | label:
            d = cats.setdefault(c, {"tp": 0, "fp": 0, "fn": 0})
            d["tp" if c in model and c in label else "fp" if c in model else "fn"] += 1
    return {"labeled": len(rows), "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "categories": dict(sorted(cats.items()))}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.eval")
    p.add_argument("command", choices=["sample", "score"])
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args(argv)
    if args.command == "sample":
        digest = load_digest(args.root / "data" / "items.json")
        out = args.root / "eval" / "to_label.csv"
        write_sample(sample(digest, args.n), out)
        print(f"wrote {out}: fill label_relevant (y/n) and label_categories, save as eval/labels.csv")
    else:
        s = score(args.root / "eval" / "labels.csv")
        print(f"labeled {s['labeled']}  precision {s['precision']}  recall {s['recall']}")
        for c, d in s["categories"].items():
            print(f"  {c:16} tp {d['tp']:3}  fp {d['fp']:3}  fn {d['fn']:3}")


if __name__ == "__main__":
    main()
