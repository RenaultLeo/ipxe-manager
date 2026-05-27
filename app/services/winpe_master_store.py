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


def normalize_master_family(raw: str) -> str:
    fam = (raw or "").strip()
    if not _SLUG_RE.match(fam):
        raise ValueError("Famille master invalide.")
    return fam


def compose_master_key(family: str, slug: str) -> str:
    return f"{normalize_master_family(family)}/{normalize_master_slug(slug)}"


def master_folder(slug: str, family: str = "w11") -> Path:
    p = masters_root() / compose_master_key(family, slug)
    p.mkdir(parents=True, exist_ok=True)
    return p


def master_wim_path(slug: str, family: str = "w11") -> Path:
    return master_folder(slug, family) / INSTALL_WIM_FILENAME


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


def upsert_master_meta(slug: str, *, family: str = "w11", label: str, wim_index: int) -> None:
    fam = normalize_master_family(family)
    s = normalize_master_slug(slug)
    key = compose_master_key(fam, s)
    lbl = (label or s).strip() or s
    idx = max(1, int(wim_index or 1))
    cat = _load_catalog()
    cat[key] = {"family": fam, "slug": s, "key": key, "label": lbl, "wim_index": idx}
    _save_catalog(cat)


def list_global_masters() -> list[dict[str, Any]]:
    cat = _load_catalog()
    out: list[dict[str, Any]] = []
    root = masters_root()
    for family_dir in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not family_dir.is_dir():
            continue
        # Compat ancien schéma: boot/masters/<slug>/install.wim
        legacy_wim = family_dir / INSTALL_WIM_FILENAME
        if legacy_wim.is_file():
            slug = family_dir.name
            key = f"w11/{slug}"
            meta = cat.get(slug) or cat.get(key) or {}
            out.append(
                {
                    "family": "w11",
                    "slug": slug,
                    "key": key,
                    "label": (meta.get("label") or slug),
                    "wim_index": max(1, int(meta.get("wim_index") or 1)),
                    "wim_path": str(legacy_wim),
                    "size": legacy_wim.stat().st_size,
                }
            )
            continue

        family = family_dir.name
        for folder in sorted(family_dir.iterdir(), key=lambda p: p.name.casefold()):
            if not folder.is_dir():
                continue
            slug = folder.name
            key = f"{family}/{slug}"
            wim = folder / INSTALL_WIM_FILENAME
            if not wim.is_file():
                continue
            meta = cat.get(key) or {}
            out.append(
                {
                    "family": family,
                    "slug": slug,
                    "key": key,
                    "label": (meta.get("label") or slug),
                    "wim_index": max(1, int(meta.get("wim_index") or 1)),
                    "wim_path": str(wim),
                    "size": wim.stat().st_size,
                }
            )
    return out
