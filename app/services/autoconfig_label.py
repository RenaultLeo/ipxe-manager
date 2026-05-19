"""Libellés menu iPXE pour les configs auto (évite le défaut générique « user-data »)."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.models import AutoConfig

_GENERIC_LABELS = frozenset({"user-data", "meta-data", "cloud-config", "cloud-init"})


def next_autoconfig_menu_label(
    db: Session,
    iso_version_id: int,
    *,
    extra: int = 0,
    exclude_id: int | None = None,
) -> str:
    """Prochain libellé menu pour une version ISO : ``config 1``, ``config 2``, …"""
    q = db.query(AutoConfig).filter(AutoConfig.iso_version_id == iso_version_id)
    if exclude_id is not None:
        q = q.filter(AutoConfig.id != exclude_id)
    count = q.count()
    return f"config {count + 1 + extra}"


def next_ubuntu_cloud_slug(db: Session, iso_version_id: int, *, extra: int = 0) -> str:
    """Slug dossier ``conf-cloudInit-<slug>/`` : ``config-1``, ``config-2``, …"""
    count = (
        db.query(AutoConfig)
        .filter(
            AutoConfig.iso_version_id == iso_version_id,
            AutoConfig.ubuntu_cloud_slug.isnot(None),
        )
        .count()
    )
    return f"config-{count + 1 + extra}"


def label_from_ubuntu_cloud_slug(slug: str) -> str:
    """``config-3`` → ``config 3`` ; sinon le slug lisible tel quel."""
    s = (slug or "").strip()
    m = re.fullmatch(r"config-(\d+)", s, flags=re.IGNORECASE)
    if m:
        return f"config {m.group(1)}"
    return s.replace("-", " ").strip() or s


def resolve_autoconfig_menu_label(ac: AutoConfig) -> str:
    """Libellé affiché dans les menus (corrige les anciennes entrées « user-data »)."""
    raw = (ac.label or "").strip()
    if raw and raw.lower() not in _GENERIC_LABELS:
        return raw
    if ac.ubuntu_cloud_slug:
        return label_from_ubuntu_cloud_slug(ac.ubuntu_cloud_slug)
    if raw:
        return raw
    ct = (ac.config_type or "").strip()
    return ct if ct and ct.lower() not in _GENERIC_LABELS else "config"


def normalize_new_config_label(
    db: Session,
    iso_version_id: int,
    label: str,
    *,
    exclude_id: int | None = None,
    ubuntu_cloud_slug: str | None = None,
) -> str:
    """Libellé saisi ou numérotation automatique si vide / générique."""
    cleaned = (label or "").strip()
    if cleaned and cleaned.lower() not in _GENERIC_LABELS:
        return cleaned
    if ubuntu_cloud_slug:
        derived = label_from_ubuntu_cloud_slug(ubuntu_cloud_slug)
        if derived and derived.lower() not in _GENERIC_LABELS:
            return derived
    return next_autoconfig_menu_label(
        db, iso_version_id, exclude_id=exclude_id
    )
