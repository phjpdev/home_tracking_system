"""Shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..reid.config import ReidConfig
from ..reid.gallery_sqlite import GallerySqliteFaiss


def load_gallery(cfg_path: Path) -> tuple[GallerySqliteFaiss, ReidConfig]:
    cfg_path = cfg_path.resolve()
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    rcfg = ReidConfig.from_cfg(cfg, cfg_path.parent)
    return GallerySqliteFaiss(rcfg), rcfg
