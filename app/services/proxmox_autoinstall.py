"""
Proxmox VE — answer.toml et injection dans proxmox-netboot.iso.

- Config : ``configs/proxmox/<version>/answer.toml`` + copie à la racine de ``boot/proxmox/<version>/``.
- Injection : xorriso ouvre ``netboot/proxmox-netboot.iso``, place ``/answer.toml`` et
  ``/auto-installer-mode.toml``, produit ``proxmox-netboot-autoinstall.iso``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import AutoConfig, IsoVersion, Upload
from app.services.autoconfig_publish import (
    PROXMOX_ANSWER_BASENAME,
    proxmox_boot_version_dir,
    publish_proxmox_answer_from_autoconfig,
)
from app.services.iso_extractor import (
    PROXMOX_NETBOOT_AUTOINSTALL_BASENAME,
    PROXMOX_NETBOOT_ISO_BASENAME,
    migrate_legacy_proxmox_netboot_isos,
)

logger = logging.getLogger(__name__)

_AUTOINSTALLER_MODE = """mode = "iso"
partition_label = "proxmox-ais"
"""


def _netboot_dir(iso_version: IsoVersion) -> Path:
    return migrate_legacy_proxmox_netboot_isos(proxmox_boot_version_dir(iso_version))


def netboot_iso_path(iso_version: IsoVersion, be=None, cfg=None) -> Path | None:
    """``proxmox-netboot.iso`` (installation manuelle, non modifiée à l’injection)."""
    p = _netboot_dir(iso_version) / PROXMOX_NETBOOT_ISO_BASENAME
    return p if p.is_file() else None


def netboot_autoinstall_iso_path(iso_version: IsoVersion, be=None, cfg=None) -> Path:
    return _netboot_dir(iso_version) / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME


def _resolve_answer_toml(iso_version: IsoVersion, cfg: AutoConfig) -> Path:
    """Fichier answer à injecter : configs/ en priorité, sinon copie sous boot/."""
    rel = (cfg.file_path or "").strip().lstrip("/")
    if rel:
        p = Path(settings.http_root) / rel.replace("\\", "/")
        if p.is_file():
            return p
    boot_copy = proxmox_boot_version_dir(iso_version) / PROXMOX_ANSWER_BASENAME
    if boot_copy.is_file():
        return boot_copy
    raise FileNotFoundError(
        f"answer.toml introuvable (configs ou {boot_copy})"
    )


def _xorriso_ok(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode in (0, 1, 5, 32)


def inject_answer_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> None:
    """Ouvre proxmox-netboot.iso, injecte answer + mode ISO, écrit proxmox-netboot-autoinstall.iso."""
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise RuntimeError("xorriso introuvable (apt install xorriso).")
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"proxmox-netboot.iso absent : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")

    out_iso.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pve-inject-") as tmp:
        tmp_dir = Path(tmp)
        mode_file = tmp_dir / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE, encoding="utf-8")
        tmp_out = tmp_dir / "out.iso"
        proc = subprocess.run(
            [
                xorriso,
                "-indev",
                str(netboot_iso),
                "-outdev",
                str(tmp_out),
                "-boot_image",
                "any",
                "replay",
                "-map",
                str(mode_file),
                "/auto-installer-mode.toml",
                "-map",
                str(answer_toml),
                "/answer.toml",
                "-commit",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if not _xorriso_ok(proc) or not tmp_out.is_file():
            blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
            raise RuntimeError(
                f"Échec xorriso ({netboot_iso.name} → {out_iso.name}) : "
                f"{blob[-1500:] if blob else proc.returncode}"
            )
        os.replace(tmp_out, out_iso)

    logger.info(
        "Proxmox : %s créé depuis %s (answer=%s)",
        out_iso.name,
        netboot_iso.name,
        answer_toml.name,
    )


def inject_active_proxmox_autoinstall(
    iso_version: IsoVersion,
    cfg: AutoConfig,
) -> None:
    """Injecte la config active dans proxmox-netboot-autoinstall.iso."""
    netboot = netboot_iso_path(iso_version)
    if not netboot:
        raise FileNotFoundError(
            "proxmox-netboot.iso absent — extraire l’ISO Proxmox sur cette version."
        )
    answer = _resolve_answer_toml(iso_version, cfg)
    out = netboot_autoinstall_iso_path(iso_version)
    inject_answer_into_netboot_iso(netboot, answer, out)


def activate_proxmox_config(db: Session, version: IsoVersion, cfg: AutoConfig) -> None:
    if (version.os_type.slug or "").lower() != "proxmox":
        raise ValueError("Réservé aux versions Proxmox VE.")
    if cfg.iso_version_id != version.id:
        raise ValueError("Cette config n’appartient pas à cette version ISO.")
    if cfg.config_type != "proxmox-answer":
        raise ValueError("Type de config invalide pour Proxmox.")
    version.active_autoconfig_id = cfg.id
    db.add(version)
    db.commit()
    publish_proxmox_answer_from_autoconfig(version, cfg)


def queue_proxmox_inject(
    db: Session,
    version: IsoVersion,
    cfg: AutoConfig,
) -> Upload:
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

    async_result = inject_proxmox_autoinstall_task.delay(
        version.id, cfg.id, upload.id
    )
    upload.task_id = async_result.id
    upload.status = "processing"
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload
