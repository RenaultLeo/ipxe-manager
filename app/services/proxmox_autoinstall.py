"""
Publication Proxmox VE « automated installation » pour boot PXE (ISO dans initrd).

L’installateur monte ``/proxmox.iso`` en loop iso9660 ; seuls ``answer.toml`` et
``auto-installer-mode.toml`` sont ajoutés/remplacés à la racine, à partir d’une
copie de base intacte (``proxmox-netboot-base.iso``).
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
from app.services.iso_extractor import (
    PROXMOX_NETBOOT_BASE_BASENAME,
    PROXMOX_NETBOOT_ISO_BASENAME,
)

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


def netboot_base_iso_path(netboot_iso: Path) -> Path:
    return netboot_iso.parent / PROXMOX_NETBOOT_BASE_BASENAME


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
    return proc.returncode in (5, 32)


def _xorriso_find(xorriso: str, iso: Path, name: str) -> bool:
    proc = subprocess.run(
        [xorriso, "-indev", str(iso), "-find", "/", "-name", name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not _xorriso_rc_ok(proc):
        return False
    blob = (proc.stdout or "") + (proc.stderr or "")
    return name in blob


def _verify_mountable_pve_iso(xorriso: str, iso: Path) -> tuple[bool, str]:
    """
    L’installateur fait ``mount -t iso9660 -o loop,ro /proxmox.iso /mnt``.
    On vérifie la présence de fichiers Proxmox attendus dans l’image.
    """
    if not _xorriso_find(xorriso, iso, ".disk/info"):
        return False, "structure ISO invalide (.disk/info absent)"
    if not _xorriso_find(xorriso, iso, "answer.toml"):
        return False, "answer.toml absent à la racine de l’ISO"
    if not _xorriso_find(xorriso, iso, "auto-installer-mode.toml"):
        return False, "auto-installer-mode.toml absent à la racine de l’ISO"
    return True, ""


def _inject_with_xorriso(
    xorriso: str,
    base_iso: Path,
    out_iso: Path,
    mode_file: Path,
    answer_copy: Path,
) -> tuple[bool, str]:
    """
    Recopie l’ISO depuis la base et remplace uniquement deux fichiers à la racine.
    ``-update`` + ``-boot_image any replay`` pour conserver le boot El Torito / hybrid.
    """
    cmd = [
        xorriso,
        "-indev",
        str(base_iso),
        "-outdev",
        str(out_iso),
        "-boot_image",
        "any",
        "replay",
        "-update",
        str(mode_file),
        "/auto-installer-mode.toml",
        "-update",
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
    ok, reason = _verify_mountable_pve_iso(xorriso, out_iso)
    if not ok:
        return False, reason
    return True, ""


def _find_pristine_proxmox_iso(iso_version: IsoVersion, cfg: Settings) -> Path | None:
    raw = (iso_version.iso_path or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            return p
    pack = Path(cfg.iso_root) / "proxmox" / str(iso_version.id)
    if pack.is_dir():
        for p in sorted(pack.glob("*.iso")):
            if p.is_file():
                return p
    return None


def _ensure_base_iso(
    netboot_iso: Path,
    iso_version: IsoVersion | None,
    cfg: Settings,
) -> Path:
    """Garantit ``proxmox-netboot-base.iso`` (copie d’origine non injectée)."""
    base = netboot_base_iso_path(netboot_iso)
    if base.is_file():
        return base
    pristine = _find_pristine_proxmox_iso(iso_version, cfg) if iso_version else None
    if pristine:
        shutil.copy2(pristine, base)
        logger.info("Proxmox : base ISO recréée depuis %s", pristine)
        return base
    xorriso = shutil.which("xorriso")
    if (
        netboot_iso.is_file()
        and xorriso
        and not _xorriso_find(xorriso, netboot_iso, "answer.toml")
    ):
        shutil.copy2(netboot_iso, base)
        logger.info("Proxmox : base ISO créée depuis netboot (sans answer.toml)")
        return base
    raise FileNotFoundError(
        "proxmox-netboot-base.iso absente — ré-extraire l’ISO Proxmox sur cette version "
        "(recrée la copie d’origine avant injection)."
    )


def inject_proxmox_autoinstall_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
    *,
    iso_version: IsoVersion | None = None,
    settings_obj: Settings | None = None,
) -> None:
    """Injecte answer.toml dans proxmox-netboot.iso à partir de la base intacte."""
    cfg = settings_obj or settings
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"ISO netboot absente : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise RuntimeError(
            "xorriso introuvable sur le serveur (apt install xorriso)."
        )

    base_iso = _ensure_base_iso(netboot_iso, iso_version, cfg)

    with tempfile.TemporaryDirectory(prefix="pve-ais-") as tmp:
        tmp_dir = Path(tmp)
        mode_file = tmp_dir / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
        answer_copy = tmp_dir / "answer.toml"
        answer_copy.write_bytes(answer_toml.read_bytes())
        out_iso = tmp_dir / "proxmox-netboot-new.iso"

        ok, err = _inject_with_xorriso(
            xorriso, base_iso, out_iso, mode_file, answer_copy
        )
        if not ok:
            logger.error(
                "Proxmox autoinstall : échec xorriso (base %s) : %s",
                base_iso.name,
                err,
            )
            raise RuntimeError(f"Échec injection ISO Proxmox : {err}")

        os.replace(out_iso, netboot_iso)

    logger.info(
        "Proxmox autoinstall : answer.toml injecté dans %s (depuis %s)",
        netboot_iso.name,
        base_iso.name,
    )


def restore_proxmox_netboot_from_base(netboot_iso: Path) -> None:
    """Restaure proxmox-netboot.iso depuis la copie de base (ISO sans answer.toml)."""
    base = netboot_base_iso_path(netboot_iso)
    if not base.is_file():
        raise FileNotFoundError(f"Base ISO absente : {base}")
    shutil.copy2(base, netboot_iso)
    logger.info("Proxmox : %s restauré depuis la base", netboot_iso.name)


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
    inject_proxmox_autoinstall_into_netboot_iso(
        netboot,
        answer_p,
        iso_version=iso_version,
        settings_obj=cfg_settings,
    )


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
