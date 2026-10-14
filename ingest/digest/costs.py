"""Dollars spent on Claude, from data/usage.jsonl: python -m digest.costs [--since YYYY-MM-DD]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def summarize(usage_path: Path, prices: dict, since: str | None = None) -> dict:
    by_purpose: dict[str, dict] = {}
    if not usage_path.exists():  # no AI run yet
        return {"by_purpose": by_purpose, "total_usd": 0.0}
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if since and r["ts"][:10] < since:
            continue
        d = by_purpose.setdefault(r["purpose"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "unknown_calls": 0})
        d["calls"] += 1
        if r.get("input_tokens") is None or r.get("output_tokens") is None:
            d["unknown_calls"] += 1  # billed call whose usage was not recorded: unknown, not zero
            continue
        p = prices.get(r["model"], {"input": 0.0, "output": 0.0})
        usd = (r["input_tokens"] * p["input"] + r["output_tokens"] * p["output"]) / 1_000_000
        d["input_tokens"] += r["input_tokens"]
        d["output_tokens"] += r["output_tokens"]
        d["usd"] = round(d["usd"] + usd, 4)
    return {"by_purpose": by_purpose, "total_usd": round(sum(d["usd"] for d in by_purpose.values()), 4)}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.costs")
    p.add_argument("--since")
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args(argv)
    prices = yaml.safe_load((args.root / "config" / "ai.yaml").read_text(encoding="utf-8"))["prices"]
    s = summarize(args.root / "data" / "usage.jsonl", prices, args.since)
    for purpose, d in s["by_purpose"].items():
        note = f"  ({d['unknown_calls']} calls with unknown usage)" if d["unknown_calls"] else ""
        print(f"{purpose:10} {d['calls']:5} calls  {d['input_tokens']:>10} in  {d['output_tokens']:>9} out  ${d['usd']:.2f}{note}")
    print(f"total ${s['total_usd']:.2f}")


if __name__ == "__main__":
    main()
