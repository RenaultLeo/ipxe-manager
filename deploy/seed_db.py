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

DEFAULT_OS = [
    {"slug": "windows", "label": "Windows",     "icon": "bi-windows",  "boot_type": "windows", "is_builtin": True},
    {"slug": "winpe",   "label": "WinPE",       "icon": "bi-terminal", "boot_type": "windows", "is_builtin": True},
    {"slug": "ubuntu",  "label": "Ubuntu",      "icon": "bi-ubuntu",   "boot_type": "linux",   "is_builtin": True},
    {"slug": "debian",  "label": "Debian",      "icon": "bi-hdd",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "centos",  "label": "CentOS",      "icon": "bi-hdd",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "rocky",   "label": "Rocky Linux", "icon": "bi-hdd",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "alma",    "label": "AlmaLinux",   "icon": "bi-hdd",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "fedora",  "label": "Fedora",      "icon": "bi-hdd",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "proxmox", "label": "Proxmox VE",  "icon": "bi-server",   "boot_type": "linux",   "is_builtin": True},
    {"slug": "esxi",    "label": "VMware ESXi", "icon": "bi-cpu",      "boot_type": "linux",   "is_builtin": True},
    {"slug": "alpine",  "label": "Alpine Linux", "icon": "bi-hdd",     "boot_type": "linux",   "is_builtin": True},
    {"slug": "tools",   "label": "Outils",      "icon": "bi-tools",    "boot_type": "linux",   "is_builtin": False},
]

if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    added = 0
    updated = 0
    for entry in DEFAULT_OS:
        existing = db.query(OsType).filter(OsType.slug == entry["slug"]).first()
        if not existing:
            db.add(OsType(**entry))
            added += 1
        else:
            # Mettre à jour is_builtin sur les OS déjà présents
            existing.is_builtin = entry["is_builtin"]
            updated += 1
    db.commit()
    db.close()
    print(f"Base initialisée — {added} ajouté(s), {updated} mis à jour.")
