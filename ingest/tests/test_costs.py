import json

from digest.costs import summarize

PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50}, "claude-sonnet-5-5": {"input": 2.00, "output": 10.00}}


def test_summarize(tmp_path):
    p = tmp_path / "usage.jsonl"
    rows = [
        {"ts": "2026-10-01T11:20:00+00:00", "purpose": "screen", "model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 0},
        {"ts": "2026-10-05T11:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": 500_000, "output_tokens": 100_000},
        {"ts": "2026-10-05T17:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": 500_000, "output_tokens": 100_000},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    s = summarize(p, PRICES)
    assert s["by_purpose"]["screen"] == {"calls": 1, "input_tokens": 1_000_000, "output_tokens": 0, "usd": 0.1, "unknown_calls": 0}
    assert s["by_purpose"]["summarize"]["usd"] == 4.0
    assert s["total_usd"] == 4.1
    assert summarize(p, PRICES, since="2026-10-05")["total_usd"] == 4.0


def test_null_tokens_count_as_unknown_not_zero_calls(tmp_path):
    p = tmp_path / "usage.jsonl"
    rows = [
        {"ts": "2026-10-05T11:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": 500_000, "output_tokens": 100_000},
        {"ts": "2026-10-05T12:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": None, "output_tokens": None},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    d = summarize(p, PRICES)["by_purpose"]["summarize"]
    assert d["calls"] == 2 and d["unknown_calls"] == 1
    assert d["input_tokens"] == 500_000 and d["usd"] == 2.0


def test_missing_usage_file_is_empty_summary(tmp_path):
    assert summarize(tmp_path / "nope.jsonl", PRICES) == {"by_purpose": {}, "total_usd": 0.0}
