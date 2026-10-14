"""Download Chicago ward and community-area boundaries into config/geo/ (run once; files are committed)."""
import json
from pathlib import Path

from digest.http import HttpFetcher

OUT = Path(__file__).resolve().parents[2] / "config" / "geo"
SETS = {
    "wards.geojson": ("https://data.cityofchicago.org/resource/p293-wvbd.geojson", "ward", 50),
    "community_areas.geojson": ("https://data.cityofchicago.org/resource/igwz-8jzy.geojson", "community", 77),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    f = HttpFetcher()
    for name, (url, prop, expected) in SETS.items():
        data = f.get_json(url, {"$limit": 500})
        feats = data["features"]
        assert len(feats) == expected, f"{name}: {len(feats)} features, expected {expected}"
        assert all(prop in ft["properties"] for ft in feats), f"{name}: missing property {prop}"
        (OUT / name).write_text(json.dumps(data), encoding="utf-8")
        print(f"{name}: {len(feats)} features")
