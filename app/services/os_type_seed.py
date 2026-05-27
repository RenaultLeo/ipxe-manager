"""Types d'OS intégrés (seed) — source de vérité partagée avec deploy/seed_db.py et les audits."""
from __future__ import annotations

from typing import Iterable

# WinPE n'est plus un OS séparé : c'est un mode de Windows (windows_mode=winpe).
LEGACY_OS_SLUGS: frozenset[str] = frozenset({"winpe"})

DEFAULT_OS: list[dict[str, object]] = [
    {"slug": "windows", "label": "Windows", "icon": "bi-windows", "boot_type": "windows", "is_builtin": True},
    {"slug": "ubuntu", "label": "Ubuntu", "icon": "bi-ubuntu", "boot_type": "linux", "is_builtin": True},
    {"slug": "debian", "label": "Debian", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "centos", "label": "CentOS", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "rocky", "label": "Rocky Linux", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "alpine", "label": "Alpine Linux", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "alma", "label": "AlmaLinux", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "fedora", "label": "Fedora", "icon": "bi-hdd", "boot_type": "linux", "is_builtin": True},
    {"slug": "proxmox", "label": "Proxmox VE", "icon": "bi-server", "boot_type": "linux", "is_builtin": True},
    {"slug": "esxi", "label": "VMware ESXi", "icon": "bi-cpu", "boot_type": "esxi", "is_builtin": True},
    {"slug": "tools", "label": "Outils", "icon": "bi-tools", "boot_type": "tools", "is_builtin": False},
]

EXPECTED_BUILTIN_OS_SLUGS: frozenset[str] = frozenset(
    str(entry["slug"]) for entry in DEFAULT_OS
)


def validate_builtin_os_slugs(slugs: Iterable[str]) -> tuple[list[str], list[str]]:
    """Retourne (slugs_seed_manquants, slugs_legacy_encore_présents)."""
    present = {(s or "").strip().lower() for s in slugs if (s or "").strip()}
    missing = sorted(EXPECTED_BUILTIN_OS_SLUGS - present)
    legacy = sorted(LEGACY_OS_SLUGS & present)
    return missing, legacy
