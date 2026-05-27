"""
Ordre d'affichage des types d'OS : onglets ISO, menus centralisés iPXE, paramètres, etc.

`ui_sort_order` en base prend le dessus après migration ; `UI_OS_SLUG_ORDER`
sert de socle lors du premier remplissage des lignes existantes.
"""
from __future__ import annotations

from typing import Iterable

from app.models.models import OsType

# Ordre métier initial — notamment Alpine juste après Rocky, puis Alma, Fedora, Proxmox.
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
    "esxi",
    "tools",
)


def sort_os_types_for_ui(os_types: Iterable[OsType]) -> list[OsType]:
    items = list(os_types)

    def key(ot: OsType) -> tuple[int, str]:
        return (getattr(ot, "ui_sort_order", 0) or 0, (ot.slug or "").lower())

    return sorted(items, key=key)


def visible_on_dashboard(os_types: Iterable[OsType]) -> list[OsType]:
    """Types d'OS dont la carte doit apparaître sur le tableau de bord."""
    filtered = (
        ot
        for ot in os_types
        if getattr(ot, "show_on_dashboard", True) not in (False, 0)
    )
    return sort_os_types_for_ui(filtered)
