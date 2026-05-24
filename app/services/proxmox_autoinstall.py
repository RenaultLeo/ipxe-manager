"""
Proxmox VE — injection answer.toml dans proxmox-netboot-autoinstall.iso.

- Config : ``configs/proxmox/<version>/answer.toml`` + copie ``boot/proxmox/<version>/answer.toml``.
- ISO : ``proxmox-auto-install-assistant prepare-iso`` (outil officiel Proxmox, requis sur le serveur).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
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

_ASSISTANT = "proxmox-auto-install-assistant"
_MIN_ISO_BYTES = 200 * 1024 * 1024


def _netboot_dir(iso_version: IsoVersion) -> Path:
    return migrate_legacy_proxmox_netboot_isos(proxmox_boot_version_dir(iso_version))


def netboot_iso_path(iso_version: IsoVersion, be=None, cfg=None) -> Path | None:
    p = _netboot_dir(iso_version) / PROXMOX_NETBOOT_ISO_BASENAME
    return p if p.is_file() else None


def netboot_autoinstall_iso_path(iso_version: IsoVersion, be=None, cfg=None) -> Path:
    return _netboot_dir(iso_version) / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME


def _resolve_answer_toml(iso_version: IsoVersion, cfg: AutoConfig) -> Path:
    rel = (cfg.file_path or "").strip().lstrip("/")
    if rel:
        p = Path(settings.http_root) / rel.replace("\\", "/")
        if p.is_file():
            return p
    boot_copy = proxmox_boot_version_dir(iso_version) / PROXMOX_ANSWER_BASENAME
    if boot_copy.is_file():
        return boot_copy
    raise FileNotFoundError(f"answer.toml introuvable (configs ou {boot_copy})")


def _require_assistant() -> str:
    exe = shutil.which(_ASSISTANT)
    if not exe:
        raise RuntimeError(
            f"{_ASSISTANT} introuvable sur le serveur — "
            "installez-le (dépôt Proxmox / paquet proxmox-auto-install-assistant)."
        )
    return exe


def prepare_autoinstall_iso(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> None:
    """Crée proxmox-netboot-autoinstall.iso depuis proxmox-netboot.iso + answer.toml."""
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"proxmox-netboot.iso absent : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")

    assistant = _require_assistant()
    src_size = netboot_iso.stat().st_size
    out_iso.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_iso.with_name(f".{out_iso.name}.preparing")

    if tmp_out.exists():
        tmp_out.unlink()

    proc = subprocess.run(
        [
            assistant,
            "prepare-iso",
            str(netboot_iso),
            "--fetch-from",
            "iso",
            "--answer-file",
            str(answer_toml),
            "--output",
            str(tmp_out),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise RuntimeError(
            f"{_ASSISTANT} prepare-iso a échoué : "
            f"{blob[-2500:] if blob else f'code {proc.returncode}'}"
        )
    if not tmp_out.is_file():
        raise RuntimeError(f"{_ASSISTANT} n'a pas produit {tmp_out.name}")

    out_size = tmp_out.stat().st_size
    if out_size < _MIN_ISO_BYTES or out_size < src_size * 0.9:
        tmp_out.unlink(missing_ok=True)
        raise RuntimeError(
            f"ISO autoinstall invalide ({out_size} o, source {src_size} o) — "
            f"vérifiez {assistant} prepare-iso --help et les logs ci-dessus."
        )

    os.replace(tmp_out, out_iso)
    logger.info(
        "Proxmox : %s préparé (%s o) depuis %s",
        out_iso.name,
        out_size,
        netboot_iso.name,
    )


def inject_active_proxmox_autoinstall(
    iso_version: IsoVersion,
    cfg: AutoConfig,
) -> None:
    netboot = netboot_iso_path(iso_version)
    if not netboot:
        raise FileNotFoundError(
            "proxmox-netboot.iso absent — extraire l’ISO Proxmox sur cette version."
        )
    prepare_autoinstall_iso(
        netboot,
        _resolve_answer_toml(iso_version, cfg),
        netboot_autoinstall_iso_path(iso_version),
    )


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
