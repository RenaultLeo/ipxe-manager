"""
Publication Proxmox VE « automated installation » pour boot PXE (ISO dans initrd).

L’installateur lit ``auto-installer-mode.toml`` et ``answer.toml`` à la racine de
``proxmox-netboot.iso`` (pas via un simple paramètre noyau).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models.models import AutoConfig, IsoVersion, Upload
from app.services.iso_extractor import PROXMOX_NETBOOT_ISO_BASENAME

logger = logging.getLogger(__name__)

_AUTOINSTALLER_MODE_ISO = """mode = "iso"
partition_label = "proxmox-ais"
"""


def _answer_toml_path(ac: AutoConfig) -> Path | None:
    rel = (ac.file_path or "").strip().lstrip("/")
    if not rel:
        return None
    p = Path(settings.http_root) / rel.replace("\\", "/")
    return p if p.is_file() else None


def _boot_version_segment(be, iso_version: IsoVersion) -> str:
    from app.services.slugify import slugify

    if be:
        for rel in (be.kernel_path, be.initrd_path):
            if not rel:
                continue
            parts = rel.replace("\\", "/").lstrip("/").split("/")
            if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == "proxmox":
                return parts[2]
    return slugify(iso_version.version_label or "")


def netboot_iso_path(
    iso_version: IsoVersion,
    be,
    cfg: Settings | None = None,
) -> Path | None:
    cfg = cfg or settings
    seg = _boot_version_segment(be, iso_version) if be else ""
    if not seg:
        seg = (iso_version.version_label or "").strip()
    if not seg:
        return None
    p = cfg.boot_dir / "proxmox" / seg / PROXMOX_NETBOOT_ISO_BASENAME
    return p if p.is_file() else None


def pick_proxmox_autoconfig(iso_version: IsoVersion) -> AutoConfig | None:
    configs = [c for c in (iso_version.autoconfigs or []) if c.config_type == "proxmox-answer"]
    if not configs:
        return None
    active_id = getattr(iso_version, "active_autoconfig_id", None)
    if active_id:
        for ac in configs:
            if ac.id == active_id:
                return ac
    return None


def inject_proxmox_autoinstall_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
) -> bool:
    """Ajoute ``auto-installer-mode.toml`` et ``answer.toml`` à la racine de l’ISO (7z)."""
    if not netboot_iso.is_file():
        logger.warning("Proxmox autoinstall : ISO netboot absente %s", netboot_iso)
        return False
    if not answer_toml.is_file():
        logger.warning("Proxmox autoinstall : answer.toml absent %s", answer_toml)
        return False
    seven_z = shutil.which("7z") or shutil.which("7za")
    if not seven_z:
        logger.error(
            "Proxmox autoinstall : 7z/7za requis (apt install p7zip-full)."
        )
        return False

    with tempfile.TemporaryDirectory(prefix="pve-ais-") as tmp:
        tmp_dir = Path(tmp)
        mode_file = tmp_dir / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
        answer_copy = tmp_dir / "answer.toml"
        answer_copy.write_bytes(answer_toml.read_bytes())

        proc = subprocess.run(
            [seven_z, "a", "-y", str(netboot_iso), str(mode_file), str(answer_copy)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            logger.error(
                "Proxmox autoinstall : échec 7z sur %s : %s",
                netboot_iso.name,
                err[:2000],
            )
            return False

    logger.info("Proxmox autoinstall : answer.toml injecté dans %s", netboot_iso)
    return True


def inject_active_proxmox_autoinstall(
    iso_version: IsoVersion,
    cfg: AutoConfig,
    *,
    settings_obj: Settings | None = None,
) -> None:
    """Injecte la config ``cfg`` (answer.toml) dans proxmox-netboot.iso."""
    cfg_settings = settings_obj or settings
    be = iso_version.boot_entry
    answer_p = _answer_toml_path(cfg)
    if not answer_p:
        raise FileNotFoundError(
            f"answer.toml introuvable : {(cfg.file_path or '').strip() or '?'}"
        )
    netboot = netboot_iso_path(iso_version, be, cfg_settings)
    if not netboot:
        raise FileNotFoundError(
            "proxmox-netboot.iso absent — extraire l’ISO Proxmox sur cette version d’abord."
        )
    if not inject_proxmox_autoinstall_into_netboot_iso(netboot, answer_p):
        raise RuntimeError("Échec injection answer.toml dans proxmox-netboot.iso (voir les logs).")


def activate_proxmox_config(db: Session, version: IsoVersion, cfg: AutoConfig) -> None:
    """Définit la config courante (injection via tâche Celery séparée)."""
    if (version.os_type.slug or "").lower() != "proxmox":
        raise ValueError("Réservé aux versions Proxmox VE.")
    if cfg.iso_version_id != version.id:
        raise ValueError("Cette config n’appartient pas à cette version ISO.")
    if cfg.config_type != "proxmox-answer":
        raise ValueError("Type de config invalide pour Proxmox.")
    version.active_autoconfig_id = cfg.id
    db.add(version)
    db.commit()


def queue_proxmox_inject(
    db: Session,
    version: IsoVersion,
    cfg: AutoConfig,
) -> Upload:
    """
    Marque ``cfg`` comme config courante et lance l’injection ISO en arrière-plan.
    Retourne l’enregistrement Upload pour le polling UI.
    """
    activate_proxmox_config(db, version, cfg)
    upload = Upload(
        filename=f"proxmox/{version.id}/{cfg.id}/answer.toml",
        file_type="proxmox_inject",
        status="pending",
        size=0,
    )
    db.add(upload)
    db.flush()

    from app.tasks.jobs import inject_proxmox_autoinstall_task

    async_result = inject_proxmox_autoinstall_task.delay(version.id, cfg.id, upload.id)
    upload.task_id = async_result.id
    upload.status = "processing"
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload
