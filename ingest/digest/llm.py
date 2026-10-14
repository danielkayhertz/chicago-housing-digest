"""Thin wrapper over client.messages.parse: one place for model names, refusals and the usage log."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError


class LLMRefused(Exception):
    """The model refused, or returned no output that validates against the schema."""


class LLM:
    def __init__(self, client, models: dict[str, str], usage_path: Path):
        self.client = client
        self.models = models
        self.usage_path = usage_path

    def parse(self, *, purpose: str, model_key: str, system: str, content, schema: type[BaseModel],
              max_tokens: int = 16000) -> BaseModel:
        model = self.models[model_key]
        try:
            resp = self.client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}], output_format=schema,
            )
        except ValidationError as e:  # truncated or malformed JSON
            self._log(purpose, model, None)  # tokens were billed but the SDK gave us no usage
            raise LLMRefused(f"{purpose}: output did not validate: {e}") from e
        self._log(purpose, model, resp.usage)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise LLMRefused(f"{purpose}: no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def _log(self, purpose: str, model: str, usage) -> None:
        """Append one usage row; usage=None logs null token counts."""
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "purpose": purpose, "model": model,
               "input_tokens": usage.input_tokens if usage else None,
               "output_tokens": usage.output_tokens if usage else None}
        with self.usage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
