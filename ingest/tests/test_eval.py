import datetime as dt

from digest.eval import sample, score, write_sample
from digest.models import CHI, CategoryHit, Digest, Enrichment, Item, Meeting

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def digest(n_rel=20, n_not=20):
    m = Meeting(id="m", source_id="s", body="B", body_id="b", start=NOW)
    items = [Item(id=f"r{i}", meeting_id="m", source_ref="x", title=f"Rel {i}",
                  enrichment=Enrichment(status="ok", housing_relevant=True, summary="s", categories=[CategoryHit(id="cha", via="model")]))
             for i in range(n_rel)]
    items += [Item(id=f"n{i}", meeting_id="m", source_ref="x", title=f"Not {i}", enrichment=Enrichment(status="screened_out"))
              for i in range(n_not)]
    items.append(Item(id="pending", meeting_id="m", source_ref="x", title="No enrichment"))
    return Digest(generated_at=NOW, meetings=[m], items=items)


def test_sample_is_balanced_and_deterministic():
    rows = sample(digest(), n=10)
    assert len(rows) == 10
    assert sum(r["model_relevant"] == "y" for r in rows) == 5
    assert "pending" not in {r["id"] for r in rows}
    assert rows == sample(digest(), n=10)
    assert rows[0]["label_relevant"] == "" and rows[0]["model_categories"] in ("cha", "")


def test_score(tmp_path):
    path = tmp_path / "labels.csv"
    rows = [
        {"id": "a", "model_relevant": "y", "model_categories": "cha", "label_relevant": "y", "label_categories": "cha"},
        {"id": "b", "model_relevant": "y", "model_categories": "cha ahpaa", "label_relevant": "n", "label_categories": ""},
        {"id": "c", "model_relevant": "n", "model_categories": "", "label_relevant": "y", "label_categories": "ahpaa"},
        {"id": "d", "model_relevant": "n", "model_categories": "", "label_relevant": "n", "label_categories": ""},
        {"id": "e", "model_relevant": "y", "model_categories": "", "label_relevant": "", "label_categories": ""},  # unlabeled
    ]
    write_sample(rows, path)
    s = score(path)
    assert s["labeled"] == 4
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert s["categories"]["cha"] == {"tp": 1, "fp": 1, "fn": 0}
    assert s["categories"]["ahpaa"] == {"tp": 0, "fp": 1, "fn": 1}
