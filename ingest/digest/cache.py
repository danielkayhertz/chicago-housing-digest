import json
from pathlib import Path
from typing import Any


class JsonCache:
    """A small JSON-file key/value cache, committed to the repo under data/cache/."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
