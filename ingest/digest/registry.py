from pathlib import Path

import yaml

from .sources.base import BaseAdapter
from .sources.clihtf import ClihtfAdapter
from .sources.dpd import DpdAdapter
from .sources.elms import ElmsAdapter
from .sources.ihda import IhdaAdapter
from .sources.legistar import LegistarAdapter
from .sources.watcher import WatcherAdapter

ADAPTERS = {"elms": ElmsAdapter, "legistar": LegistarAdapter, "dpd": DpdAdapter, "ihda": IhdaAdapter,
            "clihtf": ClihtfAdapter}


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_adapters(config_dir: Path, data_dir: Path, only: set[str] | None = None) -> list[BaseAdapter]:
    out: list[BaseAdapter] = []
    for cfg in _yaml(config_dir / "sources.yaml")["sources"]:
        if only and cfg["id"] not in only:
            continue
        if cfg["adapter"] == "watcher":
            pages = _yaml(config_dir / cfg["watch_file"])["pages"]
            out.append(WatcherAdapter(cfg, pages, data_dir / "snapshots"))
        else:
            out.append(ADAPTERS[cfg["adapter"]](cfg))
    return out
