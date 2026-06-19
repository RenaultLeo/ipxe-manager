"""
Chemins SMB / helpers pour les masters Windows (fichiers sous ``boot/masters/``).
"""
from __future__ import annotations

import re
import socket
from pathlib import Path

from app.config import settings
from app.models.models import IsoVersion
from app.services.slugify import slugify
from app.services.winpe_master_store import (
    INSTALL_WIM_FILENAME,
    compose_master_key,
    delete_master,
    master_wim_path,
    normalize_master_family,
    normalize_master_slug,
)

INSTALLS_DIRNAME = "installs"  # legacy — ne plus écrire ici
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")


def normalize_install_slug(raw: str) -> str:
    """Normalise le nom de master (stockage ``boot/masters/<famille>/<slug>/``)."""
    s = slugify(raw or "").strip().lower()
    if not s:
        raise ValueError("Identifiant du dossier invalide (lettres/chiffres uniquement).")
    if not _SLUG_RE.match(s):
        raise ValueError(
            "Identifiant du dossier invalide — utilisez des lettres minuscules, chiffres, « . », « - », « _ »."
        )
    return normalize_master_slug(s)


def version_segment(version: IsoVersion) -> str:
    be = version.boot_entry
    if be and be.boot_wim_path:
        parts = be.boot_wim_path.replace("\\", "/").strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "boot":
            return parts[2]
    return slugify(version.version_label) if version.version_label else str(version.id)


def installs_root(version: IsoVersion) -> Path:
    """Legacy — préférer ``winpe_master_store.masters_root()``."""
    os_slug = version.os_type.slug
    return settings.boot_dir / os_slug / version_segment(version) / INSTALLS_DIRNAME


def install_folder(version: IsoVersion, install_slug: str, *, family: str = "w11") -> Path:
    fam = normalize_master_family(family)
    slug = normalize_install_slug(install_slug)
    return master_wim_path(slug, fam).parent


def install_wim_path(version: IsoVersion, install_slug: str, *, family: str = "w11") -> Path:
    fam = normalize_master_family(family)
    slug = normalize_install_slug(install_slug)
    return master_wim_path(slug, fam)


def install_wim_rel_path(
    version: IsoVersion, install_slug: str, *, family: str = "w11"
) -> str:
    fam = normalize_master_family(family)
    slug = normalize_install_slug(install_slug)
    return f"boot/masters/{compose_master_key(fam, slug)}/{INSTALL_WIM_FILENAME}"


def smb_host_from_settings() -> str:
    from urllib.parse import urlparse

    from app.config import resolve_server_base_url

    base = resolve_server_base_url().rstrip("/")
    if base:
        host = urlparse(base).hostname
        if host:
            return host
    from app.config import detect_primary_ipv4

    return detect_primary_ipv4()


def smb_connect_host_for_winpe() -> str:
    from app.config import detect_primary_ipv4

    host = smb_host_from_settings().strip()
    if not host:
        return detect_primary_ipv4()
    try:
        socket.inet_pton(socket.AF_INET, host)
        return host
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(host, 445, socket.AF_INET, socket.SOCK_STREAM):
            return info[4][0]
    except OSError:
        pass
    return detect_primary_ipv4()


def smb_share_name() -> str:
    return (getattr(settings, "winpe_smb_share", None) or "boot").strip() or "boot"


def smb_unc_dir_for_master(family: str, slug: str) -> str:
    host = smb_host_from_settings()
    share = smb_share_name()
    fam = normalize_master_family(family)
    s = normalize_master_slug(slug)
    return f"\\\\{host}\\{share}\\masters\\{fam}\\{s}"


def smb_unc_install_wim(family: str, slug: str) -> str:
    return f"{smb_unc_dir_for_master(family, slug)}\\{INSTALL_WIM_FILENAME}"


def delete_install_folder(
    version: IsoVersion, install_slug: str, *, family: str = "w11"
) -> None:
    del version  # masters globaux — pas de dossier par version
    fam = normalize_master_family(family)
    slug = normalize_install_slug(install_slug)
    delete_master(slug, family=fam)
