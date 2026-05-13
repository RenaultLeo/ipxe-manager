"""
Ordre d'affichage des types d'OS : dashboard, onglets ISO, menus centralisés iPXE, etc.
Les slugs hors liste apparaissent après, triés par label.
"""
from __future__ import annotations

from typing import Iterable

from app.models.models import OsType

# Ordre métier demandé — notamment Alpine juste après Rocky, puis Alma, Fedora, Proxmox.
UI_OS_SLUG_ORDER = (
    "windows",
    "ubuntu",
    "debian",
    "centos",
    "rocky",
    "alpine",
    "alma",
    "fedora",
    "proxmox",
    "winpe",
    "esxi",
    "tools",
)

_ORDER_RANK = {slug: idx for idx, slug in enumerate(UI_OS_SLUG_ORDER)}
_AFTER_BUILTIN = len(UI_OS_SLUG_ORDER)


def sort_os_types_for_ui(os_types: Iterable[OsType]) -> list[OsType]:
    items = list(os_types)

    def key(ot: OsType) -> tuple[int, str]:
        rank = _ORDER_RANK.get(ot.slug)
        if rank is not None:
            return (rank, "")
        tail = (ot.label or ot.slug).lower()
        return (_AFTER_BUILTIN, tail)

    return sorted(items, key=key)
