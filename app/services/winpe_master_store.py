"""Stockage global des masters Windows (persistants hors versions WinPE)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.winpe_installs import INSTALL_WIM_FILENAME

_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
CATALOG_FILENAME = "masters.json"


def masters_root() -> Path:
    p = settings.boot_dir / "masters"
    p.mkdir(parents=True, exist_ok=True)
    return p


def catalog_path() -> Path:
    return masters_root() / CATALOG_FILENAME


def normalize_master_slug(raw: str) -> str:
    slug = (raw or "").strip()
    if not _SLUG_RE.match(slug):
        raise ValueError("Slug master invalide.")
    return slug


def master_folder(slug: str) -> Path:
    p = masters_root() / normalize_master_slug(slug)
    p.mkdir(parents=True, exist_ok=True)
    return p


def master_wim_path(slug: str) -> Path:
    return master_folder(slug) / INSTALL_WIM_FILENAME


def _load_catalog() -> dict[str, dict[str, Any]]:
    cp = catalog_path()
    if not cp.is_file():
        return {}
    try:
        raw = json.loads(cp.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_catalog(cat: dict[str, dict[str, Any]]) -> None:
    catalog_path().write_text(
        json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def upsert_master_meta(slug: str, *, label: str, wim_index: int) -> None:
    s = normalize_master_slug(slug)
    lbl = (label or s).strip() or s
    idx = max(1, int(wim_index or 1))
    cat = _load_catalog()
    cat[s] = {"slug": s, "label": lbl, "wim_index": idx}
    _save_catalog(cat)


def list_global_masters() -> list[dict[str, Any]]:
    cat = _load_catalog()
    out: list[dict[str, Any]] = []
    root = masters_root()
    for folder in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not folder.is_dir():
            continue
        slug = folder.name
        wim = folder / INSTALL_WIM_FILENAME
        if not wim.is_file():
            continue
        meta = cat.get(slug) or {}
        out.append(
            {
                "slug": slug,
                "label": (meta.get("label") or slug),
                "wim_index": max(1, int(meta.get("wim_index") or 1)),
                "wim_path": str(wim),
                "size": wim.stat().st_size,
            }
        )
    return out
