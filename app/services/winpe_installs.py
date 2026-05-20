"""
Images install.wim WinPE : une entrée = un dossier ``installs/<slug>/install.wim``.
Le slug du dossier porte le nom de l'édition (toutes les WIM s'appellent install.wim).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config import settings
from app.models.models import IsoVersion, WinpeInstall
from app.services.slugify import slugify

INSTALLS_DIRNAME = "installs"
INSTALL_WIM_FILENAME = "install.wim"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")


def normalize_install_slug(raw: str) -> str:
    s = slugify(raw or "").strip().lower()
    if not s:
        raise ValueError("Identifiant du dossier invalide (lettres/chiffres uniquement).")
    if not _SLUG_RE.match(s):
        raise ValueError(
            "Identifiant du dossier invalide — utilisez des lettres minuscules, chiffres, « . », « - », « _ »."
        )
    return s


def version_segment(version: IsoVersion) -> str:
    be = version.boot_entry
    if be and be.boot_wim_path:
        parts = be.boot_wim_path.replace("\\", "/").strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "boot":
            return parts[2]
    return slugify(version.version_label) if version.version_label else str(version.id)


def installs_root(version: IsoVersion) -> Path:
    os_slug = version.os_type.slug
    return settings.boot_dir / os_slug / version_segment(version) / INSTALLS_DIRNAME


def install_folder(version: IsoVersion, install_slug: str) -> Path:
    return installs_root(version) / install_slug


def install_wim_path(version: IsoVersion, install_slug: str) -> Path:
    return install_folder(version, install_slug) / INSTALL_WIM_FILENAME


def install_wim_rel_path(version: IsoVersion, install_slug: str) -> str:
    os_slug = version.os_type.slug
    seg = version_segment(version)
    return f"boot/{os_slug}/{seg}/{INSTALLS_DIRNAME}/{install_slug}/{INSTALL_WIM_FILENAME}"


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


def smb_share_name() -> str:
    return (getattr(settings, "winpe_smb_share", None) or "boot").strip() or "boot"


def smb_unc_dir_for_install(version: IsoVersion, install_slug: str) -> str:
    """Répertoire SMB de l'image : ``\\\\host\\boot\\winpe\\ver\\installs\\slug``."""
    host = smb_host_from_settings()
    share = smb_share_name()
    os_slug = version.os_type.slug
    seg = version_segment(version)
    return f"\\\\{host}\\{share}\\{os_slug}\\{seg}\\{INSTALLS_DIRNAME}\\{install_slug}"


def smb_unc_install_wim(version: IsoVersion, install: WinpeInstall) -> str:
    return f"{smb_unc_dir_for_install(version, install.slug)}\\{INSTALL_WIM_FILENAME}"


def delete_install_folder(version: IsoVersion, install_slug: str) -> None:
    folder = install_folder(version, install_slug)
    if folder.is_dir():
        shutil.rmtree(folder)


def list_installs_on_disk(version: IsoVersion) -> list[Path]:
    root = installs_root(version)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / INSTALL_WIM_FILENAME).is_file():
            out.append(d)
    return out
