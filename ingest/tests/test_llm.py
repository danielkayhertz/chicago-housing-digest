import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from digest.llm import LLM, LLMRefused


class Verdict(BaseModel):
    ok: bool


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def make(tmp_path, stop_reason="end_turn", parsed=Verdict(ok=True)):
    resp = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed,
                           usage=SimpleNamespace(input_tokens=120, output_tokens=15))
    msgs = FakeMessages(resp)
    llm = LLM(SimpleNamespace(messages=msgs), {"screen": "claude-haiku-5-5"}, tmp_path / "usage.jsonl")
    return llm, msgs


def test_parse_passes_schema_and_logs_usage(tmp_path):
    llm, msgs = make(tmp_path)
    out = llm.parse(purpose="screen", model_key="screen", system="sys", content="hello", schema=Verdict, max_tokens=8000)
    assert out == Verdict(ok=True)
    assert msgs.kwargs["model"] == "claude-haiku-5-5"
    assert msgs.kwargs["output_format"] is Verdict
    assert msgs.kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert msgs.kwargs["max_tokens"] == 8000 and msgs.kwargs["system"] == "sys"
    row = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (row["purpose"], row["model"], row["input_tokens"], row["output_tokens"]) == ("screen", "claude-haiku-5-5", 120, 15)


def test_refusal_raises_and_still_logs(tmp_path):
    llm, _ = make(tmp_path, stop_reason="refusal", parsed=None)
    with pytest.raises(LLMRefused):
        llm.parse(purpose="screen", model_key="screen", system="s", content="c", schema=Verdict)
    assert (tmp_path / "usage.jsonl").exists()


def test_missing_parsed_output_raises(tmp_path):
    llm, _ = make(tmp_path, stop_reason="max_tokens", parsed=None)
    with pytest.raises(LLMRefused, match="max_tokens"):
        llm.parse(purpose="screen", model_key="screen", system="s", content="c", schema=Verdict)
