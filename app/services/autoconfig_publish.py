"""
Publication d'une config Ubuntu cloud-init vers l'arborescence boot extraite (NFS / nocloud).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import settings
from app.models.models import AutoConfig, IsoVersion
from app.services.config_scanner import UBUNTU_CLOUD_BUNDLE_PREFIX
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

UBUNTU_OS_SLUG = "ubuntu"


def boot_version_segment(version: IsoVersion) -> str:
    """Nom du dossier sous boot/ubuntu/ (aligné sur kernel_path après extraction)."""
    be = version.boot_entry
    if be:
        for rel in (be.kernel_path, be.initrd_path):
            if not rel:
                continue
            parts = rel.replace("\\", "/").lstrip("/").split("/")
            if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == UBUNTU_OS_SLUG:
                return parts[2]
    return slugify(version.version_label)


def boot_version_dir(version: IsoVersion) -> Path:
    return settings.boot_dir / UBUNTU_OS_SLUG / boot_version_segment(version)


def published_seed_dir_rel_path(version: IsoVersion) -> str:
    """Répertoire HTTP de la release (user-data + meta-data à la racine)."""
    seg = boot_version_segment(version)
    return f"boot/{UBUNTU_OS_SLUG}/{seg}"


def config_bundle_dir(cfg: AutoConfig) -> Path | None:
    if not cfg.file_path or not cfg.ubuntu_cloud_slug:
        return None
    root = Path(settings.http_root)
    bundle = root / cfg.file_path.strip("/").replace("\\", "/")
    if bundle.is_dir():
        return bundle
    return None


def clear_ubuntu_seed_from_boot(boot_dir: Path) -> None:
    """Retire user-data / meta-data et dossiers conf-cloudInit-* de la release extraite."""
    if not boot_dir.is_dir():
        return
    for name in ("user-data", "meta-data"):
        f = boot_dir / name
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                logger.exception("Suppression %s", f)
    for sub in list(boot_dir.iterdir()):
        if sub.is_dir() and sub.name.startswith(UBUNTU_CLOUD_BUNDLE_PREFIX):
            shutil.rmtree(sub, ignore_errors=True)


def publish_ubuntu_cloud_config(version: IsoVersion, cfg: AutoConfig) -> str:
    """
    Copie user-data + meta-data à la racine de boot/ubuntu/<release>/ (pas de sous-dossier conf).
    Retourne le chemin relatif du répertoire seed (pour ds=nocloud;s=…/).
    """
    if cfg.config_type != "cloud-init" or not cfg.ubuntu_cloud_slug:
        raise ValueError("Config Ubuntu cloud-init (bundle) requise.")
    src = config_bundle_dir(cfg)
    if not src or not (src / "user-data").is_file() or not (src / "meta-data").is_file():
        raise FileNotFoundError(
            f"Bundle source incomplet : {cfg.file_path or '—'}"
        )

    boot_dir = boot_version_dir(version)
    if not boot_dir.is_dir():
        raise FileNotFoundError(
            f"Release boot absente : {boot_dir} — extraire l'ISO d'abord."
        )

    clear_ubuntu_seed_from_boot(boot_dir)
    shutil.copy2(src / "user-data", boot_dir / "user-data")
    shutil.copy2(src / "meta-data", boot_dir / "meta-data")

    rel = published_seed_dir_rel_path(version)
    logger.info(
        "Config Ubuntu publiée (user-data, meta-data) vers %s/ (version %s)",
        rel,
        version.id,
    )
    return rel


def activate_ubuntu_config(db, version: IsoVersion, cfg: AutoConfig) -> str:
    """Définit la config courante et la publie sous boot/. Retourne le chemin relatif publié."""
    if version.os_type.slug != UBUNTU_OS_SLUG:
        raise ValueError("Publication réservée aux versions Ubuntu.")
    if cfg.iso_version_id != version.id:
        raise ValueError("Cette config n'appartient pas à cette version ISO.")
    rel = publish_ubuntu_cloud_config(version, cfg)
    version.active_autoconfig_id = cfg.id
    db.add(version)
    db.commit()
    return rel


def clear_active_ubuntu_publish(db, version: IsoVersion) -> None:
    """Retire la config courante et nettoie les seeds dans boot/."""
    version.active_autoconfig_id = None
    db.add(version)
    db.commit()
    clear_ubuntu_seed_from_boot(boot_version_dir(version))


# ── Proxmox VE (answer.toml) ─────────────────────────────────────────────────

PROXMOX_OS_SLUG = "proxmox"
PROXMOX_ANSWER_BASENAME = "answer.toml"


def proxmox_boot_version_segment(version: IsoVersion) -> str:
    """Nom du dossier sous boot/proxmox/ (aligné sur kernel_path après extraction)."""
    be = version.boot_entry
    if be:
        for rel in (be.kernel_path, be.initrd_path):
            if not rel:
                continue
            parts = rel.replace("\\", "/").lstrip("/").split("/")
            if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == PROXMOX_OS_SLUG:
                return parts[2]
    return slugify(version.version_label)


def proxmox_boot_version_dir(version: IsoVersion) -> Path:
    return settings.boot_dir / PROXMOX_OS_SLUG / proxmox_boot_version_segment(version)


def clear_proxmox_answer_from_boot(version: IsoVersion) -> None:
    """Retire answer.toml à la racine de boot/proxmox/<release>/ (+ ancien config/)."""
    boot_dir = proxmox_boot_version_dir(version)
    if not boot_dir.is_dir():
        return
    for rel in (PROXMOX_ANSWER_BASENAME, f"config/{PROXMOX_ANSWER_BASENAME}"):
        p = boot_dir / rel
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                logger.exception("Suppression %s", p)


def publish_proxmox_answer_config(version: IsoVersion, content: str) -> str:
    """Copie answer.toml à la racine de boot/proxmox/<release>/ (miroir de configs/)."""
    boot_dir = proxmox_boot_version_dir(version)
    boot_dir.mkdir(parents=True, exist_ok=True)
    (boot_dir / PROXMOX_ANSWER_BASENAME).write_text(content, encoding="utf-8")
    rel = f"boot/{PROXMOX_OS_SLUG}/{proxmox_boot_version_segment(version)}"
    logger.info("Proxmox answer.toml → %s/%s (version %s)", rel, PROXMOX_ANSWER_BASENAME, version.id)
    return rel


def publish_proxmox_answer_from_autoconfig(
    version: IsoVersion, cfg: AutoConfig
) -> str | None:
    """Synchronise boot/ depuis le fichier configs/ ou le contenu en base."""
    if cfg.config_type != "proxmox-answer":
        return None
    content: str | None = None
    rel = (cfg.file_path or "").strip().lstrip("/")
    if rel:
        src = Path(settings.http_root) / rel.replace("\\", "/")
        if src.is_file():
            content = src.read_text(encoding="utf-8", errors="replace")
    if content is None and (cfg.content or "").strip():
        content = cfg.content
    if content is None:
        return None
    return publish_proxmox_answer_config(version, content)


def clear_active_proxmox_publish(db, version: IsoVersion) -> None:
    """Retire la config courante et nettoie answer.toml sous boot/."""
    version.active_autoconfig_id = None
    db.add(version)
    db.commit()
    clear_proxmox_answer_from_boot(version)
