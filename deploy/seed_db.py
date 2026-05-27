"""
Initialise la base de données avec les types d'OS par défaut.
Peut être relancé sans risque : ne recrée pas les entrées existantes,
mais met à jour is_builtin sur les OS de base.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import init_db, SessionLocal
from app.models.models import OsType
from app.services.os_type_order import UI_OS_SLUG_ORDER
from app.services.os_type_seed import DEFAULT_OS

_SLUG_ORDER_RANK = {s: i for i, s in enumerate(UI_OS_SLUG_ORDER)}

# Extraction ISO complète (xorriso/7z → boot/<os>/<version>/…) pour install réseau
_FULL_EXTRACT_SLUGS = frozenset(
    {
        "windows",
        "ubuntu",
        "debian",
        "rocky",
        "alma",
        "centos",
        "fedora",
        "proxmox",
        "esxi",
    }
)

if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    added = 0
    updated = 0
    for entry in DEFAULT_OS:
        slug = entry["slug"]
        ui_order = _SLUG_ORDER_RANK.get(slug, len(_SLUG_ORDER_RANK))
        existing = db.query(OsType).filter(OsType.slug == slug).first()
        if not existing:
            db.add(
                OsType(
                    **entry,
                    ui_sort_order=ui_order,
                    show_on_dashboard=True,
                    extract_full_iso=slug in _FULL_EXTRACT_SLUGS,
                )
            )
            added += 1
        else:
            # Mettre à jour is_builtin / boot_type depuis la liste de référence
            existing.is_builtin = entry["is_builtin"]
            existing.boot_type = entry["boot_type"]
            if slug in _FULL_EXTRACT_SLUGS:
                existing.extract_full_iso = True
            updated += 1
    db.commit()
    db.close()
    print(f"Base initialisée — {added} ajouté(s), {updated} mis à jour.")
