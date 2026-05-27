"""
Types de configuration automatique (liste prédéfinie + types issus de la BDD).

Séparé des routeurs pour éviter un import circulaire settings <-> configs (502 au démarrage).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.i18n import translate
from app.models.models import AutoConfig, OsType

CONFIG_TYPES = [
    "preseed",
    "kickstart",
    "esxi-kickstart",
    "unattend",
    "cloud-init",
    "proxmox-answer",
    "alpine-answer",
    "custom",
]


def all_config_types_for_ui(db: Session) -> list[str]:
    """Types prédéfinis puis types utilisés en base (forçage OS ou configs existantes)."""
    seen = set(CONFIG_TYPES)
    for (val,) in (
        db.query(OsType.forced_autoconfig_type)
        .filter(
            OsType.forced_autoconfig_type.isnot(None),
            OsType.forced_autoconfig_type != "",
        )
        .distinct()
        .all()
    ):
        s = str(val).strip()
        if s:
            seen.add(s)
    for (val,) in (
        db.query(AutoConfig.config_type)
        .filter(
            AutoConfig.config_type.isnot(None),
            AutoConfig.config_type != "",
        )
        .distinct()
        .all()
    ):
        s = str(val).strip()
        if s:
            seen.add(s)
    custom = sorted((seen - set(CONFIG_TYPES)), key=str.casefold)
    return list(CONFIG_TYPES) + custom


def config_type_labels(lang: str, types: list[str] | None = None) -> dict[str, str]:
    """Libellés du select « type » (traduction pour les entrées ayant une clé i18n)."""
    iterable = list(types) if types is not None else list(CONFIG_TYPES)
    out: dict[str, str] = {}
    for ct in iterable:
        key = "cfg.type_dd_" + ct.replace("-", "_")
        lab = translate(lang, key)
        out[ct] = ct if lab == key else lab
    return out
