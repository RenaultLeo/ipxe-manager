"""
Initialise la base de données avec les types d'OS par défaut.
Exécuter une seule fois après la première installation.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import init_db, SessionLocal
from app.models.models import OsType

DEFAULT_OS = [
    {"slug": "windows", "label": "Windows",    "icon": "bi-windows",  "boot_type": "windows"},
    {"slug": "ubuntu",  "label": "Ubuntu",     "icon": "bi-ubuntu",   "boot_type": "linux"},
    {"slug": "debian",  "label": "Debian",     "icon": "bi-hdd",      "boot_type": "linux"},
    {"slug": "centos",  "label": "CentOS",     "icon": "bi-hdd",      "boot_type": "linux"},
    {"slug": "rocky",   "label": "Rocky Linux","icon": "bi-hdd",      "boot_type": "linux"},
    {"slug": "fedora",  "label": "Fedora",     "icon": "bi-hdd",      "boot_type": "linux"},
    {"slug": "proxmox", "label": "Proxmox VE", "icon": "bi-server",   "boot_type": "linux"},
    {"slug": "esxi",    "label": "VMware ESXi","icon": "bi-cpu",      "boot_type": "linux"},
    {"slug": "winpe",   "label": "WinPE",      "icon": "bi-terminal", "boot_type": "windows"},
    {"slug": "tools",   "label": "Outils",     "icon": "bi-tools",    "boot_type": "linux"},
]

if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    added = 0
    for entry in DEFAULT_OS:
        if not db.query(OsType).filter(OsType.slug == entry["slug"]).first():
            db.add(OsType(**entry))
            added += 1
    db.commit()
    db.close()
    print(f"Base initialisée — {added} types d'OS ajoutés.")
