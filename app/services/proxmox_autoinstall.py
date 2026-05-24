"""
Publication Proxmox VE « automated installation » pour boot PXE (ISO dans initrd).

L’installateur lit ``auto-installer-mode.toml`` et ``answer.toml`` à la racine de
``proxmox-netboot.iso`` (pas via un simple paramètre noyau).
"""
from __future__ import annotations

import logging
import os
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


def _xorriso_rc_ok(proc: subprocess.CompletedProcess[str]) -> bool:
    if proc.returncode in (0, 1):
        return True
    # xorriso : avertissements fréquents (codes 5, 32) sans échec bloquant
    return proc.returncode in (5, 32)


def _inject_with_xorriso(
    xorriso: str,
    netboot_iso: Path,
    mode_file: Path,
    answer_copy: Path,
) -> tuple[bool, str]:
    """Réécrit l’ISO via xorriso (7z ne peut pas modifier les ISO9660 : E_NOTIMPL)."""
    with tempfile.TemporaryDirectory(prefix="pve-ais-") as tmp:
        out_iso = Path(tmp) / "proxmox-netboot-new.iso"
        cmd = [
            xorriso,
            "-indev",
            str(netboot_iso),
            "-outdev",
            str(out_iso),
            "-boot_image",
            "any",
            "replay",
            "-map",
            str(mode_file),
            "/auto-installer-mode.toml",
            "-map",
            str(answer_copy),
            "/answer.toml",
            "-commit",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if not _xorriso_rc_ok(proc):
            blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
            return False, blob[-2000:] if blob else f"code {proc.returncode}"
        if not out_iso.is_file() or out_iso.stat().st_size < 1024:
            return False, "xorriso n’a pas produit d’ISO de sortie"
        os.replace(out_iso, netboot_iso)
    return True, ""


def inject_proxmox_autoinstall_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
) -> None:
    """Ajoute ``auto-installer-mode.toml`` et ``answer.toml`` à la racine de l’ISO (xorriso)."""
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"ISO netboot absente : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise RuntimeError(
            "xorriso introuvable sur le serveur (apt install xorriso)."
        )

    with tempfile.TemporaryDirectory(prefix="pve-ais-") as tmp:
        tmp_dir = Path(tmp)
        mode_file = tmp_dir / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
        answer_copy = tmp_dir / "answer.toml"
        answer_copy.write_bytes(answer_toml.read_bytes())

        ok, err = _inject_with_xorriso(xorriso, netboot_iso, mode_file, answer_copy)
        if not ok:
            logger.error(
                "Proxmox autoinstall : échec xorriso sur %s : %s",
                netboot_iso.name,
                err,
            )
            raise RuntimeError(f"Échec xorriso sur proxmox-netboot.iso : {err}")

    logger.info("Proxmox autoinstall : answer.toml injecté dans %s", netboot_iso)


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
    inject_proxmox_autoinstall_into_netboot_iso(netboot, answer_p)


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
