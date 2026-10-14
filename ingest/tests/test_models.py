import datetime as dt
import json

import jsonschema

from digest.models import CHI, Attachment, Digest, Item, Meeting, dump_digest, load_digest
from digest.schema import SCHEMA_PATH, schema_text

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def sample_digest() -> Digest:
    m = Meeting(
        id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
        start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI), agenda_text="not serialized",
    )
    it = Item(id="cpc:2026-10-15#1133-w-kinzie-st", meeting_id=m.id, source_ref="1133 W. Kinzie St.",
              title="A proposed Planned Development", attachments=[Attachment(label="PD Application", url="https://x/a.pdf")])
    return Digest(generated_at=NOW, meetings=[m], items=[it])


def test_digest_roundtrip_excludes_agenda_text(tmp_path):
    path = tmp_path / "items.json"
    dump_digest(sample_digest(), path)
    text = path.read_text(encoding="utf-8")
    assert "agenda_text" not in text
    back = load_digest(path)
    assert back.items[0].title == "A proposed Planned Development"
    assert back.meetings[0].agenda_text is None


def test_load_missing_returns_none(tmp_path):
    assert load_digest(tmp_path / "nope.json") is None


def test_start_serializes_with_chicago_offset():
    assert '"2026-10-15T10:00:00-05:00"' in sample_digest().model_dump_json()


def test_schema_committed_matches_models():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text()


def test_sample_validates_against_schema():
    instance = json.loads(sample_digest().model_dump_json())
    jsonschema.validate(instance, json.loads(schema_text()))
