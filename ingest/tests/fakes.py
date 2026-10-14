import json
from pathlib import Path
from urllib.parse import unquote_plus

from digest.http import url_key

FIX = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Routes requests by substring of the decoded URL; first matching route wins.

    A route target is a fixture path relative to tests/fixtures, raw bytes, or an Exception to raise.
    """

    def __init__(self, routes: list[tuple[str, object]]):
        self.routes = routes
        self.calls: list[str] = []

    def get_bytes(self, url, params=None):
        key = unquote_plus(url_key(url, params))
        self.calls.append(key)
        for needle, target in self.routes:
            if needle in key:
                if isinstance(target, Exception):
                    raise target
                if isinstance(target, bytes):
                    return target
                return (FIX / target).read_bytes()
        raise AssertionError(f"FakeFetcher: no route for {key}")

    def get_text(self, url, params=None):
        return self.get_bytes(url, params).decode("utf-8")

    def get_json(self, url, params=None):
        return json.loads(self.get_bytes(url, params))


class FakeLLM:
    """Stands in for digest.llm.LLM. responses maps purpose -> list (popped in order) or callable(content)."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []

    def parse(self, *, purpose, model_key, system, content, schema, max_tokens=16000):
        self.calls.append({"purpose": purpose, "model_key": model_key, "system": system, "content": content})
        r = self.responses[purpose]
        value = r(content) if callable(r) else r.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
