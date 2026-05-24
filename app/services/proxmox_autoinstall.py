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

# Format attendu par proxmox-fetch-answer (mode « answer inclus dans l’ISO »).
_AUTOINSTALLER_MODE_ISO = 'mode = "iso"\n'


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


def _xorriso_root_file_exists(xorriso: str, iso: Path, iso_path: str) -> bool:
    proc = subprocess.run(
        [xorriso, "-indev", str(iso), "-ls", iso_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not _xorriso_ok(proc):
        return False
    return bool((proc.stdout or "").strip())


def _verify_autoinstall_iso(xorriso: str, iso: Path, src_size: int) -> None:
    out_size = iso.stat().st_size
    if out_size < src_size * 0.9:
        raise RuntimeError(
            f"ISO autoinstall tronquée ({out_size} o, source {src_size} o)"
        )
    for path in ("/answer.toml", "/auto-installer-mode.toml"):
        if not _xorriso_root_file_exists(xorriso, iso, path):
            raise RuntimeError(
                f"{path.strip('/')} absent à la racine de l’ISO après injection — "
                "installez proxmox-auto-install-assistant ou vérifiez xorriso."
            )


def _inject_with_proxmox_assistant(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> bool:
    """Outil officiel Proxmox (recommandé) : prepare-iso --fetch-from iso."""
    assistant = shutil.which("proxmox-auto-install-assistant")
    if not assistant:
        return False
    tmp_out = out_iso.with_name(f".{out_iso.name}.assistant")
    try:
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
        if proc.returncode != 0 or not tmp_out.is_file():
            blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
            logger.warning(
                "proxmox-auto-install-assistant prepare-iso échoué : %s",
                blob[-2000:] if blob else proc.returncode,
            )
            tmp_out.unlink(missing_ok=True)
            return False
        os.replace(tmp_out, out_iso)
        logger.info(
            "Proxmox : %s préparé via proxmox-auto-install-assistant",
            out_iso.name,
        )
        return True
    except Exception:
        logger.exception("proxmox-auto-install-assistant prepare-iso")
        tmp_out.unlink(missing_ok=True)
        return False


def _inject_with_xorriso(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> None:
    """Copie l’ISO puis xorriso -map (indev=source, outdev=copie préremplie)."""
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise RuntimeError(
            "xorriso introuvable — apt install xorriso "
            "(ou proxmox-auto-install-assistant pour l’injection officielle)."
        )

    src_size = netboot_iso.stat().st_size
    tmp_out = out_iso.with_name(f".{out_iso.name}.injecting")
    try:
        shutil.copy2(netboot_iso, tmp_out)
        with tempfile.TemporaryDirectory(prefix="pve-inject-") as tmp:
            mode_file = Path(tmp) / "auto-installer-mode.toml"
            mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
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
        if not _xorriso_ok(proc):
            blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
            raise RuntimeError(
                f"Échec xorriso ({netboot_iso.name} → {out_iso.name}) : "
                f"{blob[-1500:] if blob else proc.returncode}"
            )
        _verify_autoinstall_iso(xorriso, tmp_out, src_size)
        os.replace(tmp_out, out_iso)
    except Exception:
        tmp_out.unlink(missing_ok=True)
        raise


def inject_answer_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> None:
    """Copie proxmox-netboot.iso → autoinstall, puis injecte answer.toml + mode ISO."""
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"proxmox-netboot.iso absent : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")

    out_iso.parent.mkdir(parents=True, exist_ok=True)
    if _inject_with_proxmox_assistant(netboot_iso, answer_toml, out_iso):
        xorriso = shutil.which("xorriso")
        if xorriso:
            _verify_autoinstall_iso(
                xorriso, out_iso, netboot_iso.stat().st_size
            )
        return

    _inject_with_xorriso(netboot_iso, answer_toml, out_iso)
    logger.info(
        "Proxmox : %s créé (%s o) depuis %s (xorriso, answer=%s)",
        out_iso.name,
        out_iso.stat().st_size,
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
