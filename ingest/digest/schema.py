"""Generate schema/items.schema.json from the Digest model: python -m digest.schema"""
import json
from pathlib import Path

from .models import Digest

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "items.schema.json"


def schema_text() -> str:
    return json.dumps(Digest.model_json_schema(mode="serialization"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(schema_text(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
