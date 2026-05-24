"""
Publication Proxmox VE « automated installation » pour boot PXE (ISO dans initrd).

- ``proxmox-netboot.iso`` : ISO d’origine (menu « installation manuelle »).
- ``proxmox-netboot-autoinstall.iso`` : même ISO + answer.toml (auto-install).
- ``proxmox-netboot-base.iso`` : copie source pour régénérer l’autoinstall.
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
    PROXMOX_NETBOOT_AUTOINSTALL_BASENAME,
    PROXMOX_NETBOOT_BASE_BASENAME,
    PROXMOX_NETBOOT_DIRNAME,
    PROXMOX_NETBOOT_ISO_BASENAME,
    _NETBOOT_ARTIFACT_BASENAMES,
    migrate_legacy_proxmox_netboot_isos,
    proxmox_netboot_dir,
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


def _proxmox_boot_dir(
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
    return cfg.boot_dir / "proxmox" / seg


def _netboot_dir_for_version(
    iso_version: IsoVersion,
    be,
    cfg: Settings | None = None,
) -> Path | None:
    extract_dest = _proxmox_boot_dir(iso_version, be, cfg)
    if not extract_dest:
        return None
    return migrate_legacy_proxmox_netboot_isos(extract_dest)


def netboot_iso_path(
    iso_version: IsoVersion,
    be,
    cfg: Settings | None = None,
) -> Path | None:
    """ISO sans autoconfig (installation manuelle)."""
    netboot = _netboot_dir_for_version(iso_version, be, cfg)
    if not netboot:
        return None
    p = netboot / PROXMOX_NETBOOT_ISO_BASENAME
    return p if p.is_file() else None


def netboot_autoinstall_iso_path(
    iso_version: IsoVersion,
    be,
    cfg: Settings | None = None,
) -> Path:
    """Chemin de l’ISO autoinstall (créé à l’injection)."""
    cfg = cfg or settings
    netboot = _netboot_dir_for_version(iso_version, be, cfg)
    if not netboot:
        seg = _boot_version_segment(be, iso_version) if be else "unknown"
        netboot = proxmox_netboot_dir(cfg.boot_dir / "proxmox" / seg)
        netboot.mkdir(parents=True, exist_ok=True)
    return netboot / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME


def netboot_base_iso_path(netboot_dir: Path) -> Path:
    return netboot_dir / PROXMOX_NETBOOT_BASE_BASENAME


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


def _extracted_tree_has_installer(extract_dest: Path) -> bool:
    """Arborescence extraite sous boot/proxmox/<version>/ (preuve que l’ISO source est valide)."""
    return (extract_dest / "boot" / "initrd.img").is_file() or (
        extract_dest / "boot" / "linux26"
    ).is_file()


def _xorriso_find_name(xorriso: str, iso: Path, name: str) -> bool:
    proc = subprocess.run(
        [xorriso, "-indev", str(iso), "-find", "/", "-name", name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not _xorriso_rc_ok(proc):
        return False
    return name in (proc.stdout or "")


def _xorriso_boot_dir_ok(xorriso: str, iso: Path) -> bool:
    proc = subprocess.run(
        [xorriso, "-indev", str(iso), "-ls", "/boot"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not _xorriso_rc_ok(proc):
        return False
    out = (proc.stdout or "").lower()
    return "initrd" in out or "linux26" in out or "vmlinuz" in out


def _xorriso_path_exists(xorriso: str, iso: Path, iso_path: str) -> bool:
    proc = subprocess.run(
        [xorriso, "-indev", str(iso), "-ls", iso_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not _xorriso_rc_ok(proc):
        return False
    return bool((proc.stdout or "").strip())


def _verify_pve_iso_structure(
    xorriso: str,
    iso: Path,
    *,
    extract_dest: Path | None = None,
    require_autoinstall: bool = False,
) -> tuple[bool, str]:
    if extract_dest and _extracted_tree_has_installer(extract_dest):
        boot_ok = True
    else:
        boot_ok = (
            _xorriso_find_name(xorriso, iso, "initrd.img")
            or _xorriso_find_name(xorriso, iso, "linux26")
            or _xorriso_boot_dir_ok(xorriso, iso)
        )
    if not boot_ok:
        return False, "structure ISO invalide (fichiers boot absents dans l’ISO)"
    disk_ok = _xorriso_path_exists(xorriso, iso, "/.disk") or _xorriso_find_name(
        xorriso, iso, "info"
    )
    if not disk_ok and not (extract_dest and (extract_dest / ".disk").is_dir()):
        return False, "structure ISO invalide (/.disk absent)"
    if require_autoinstall:
        if not _xorriso_path_exists(xorriso, iso, "/answer.toml"):
            return False, "answer.toml absent à la racine de l’ISO"
        if not _xorriso_path_exists(xorriso, iso, "/auto-installer-mode.toml"):
            return False, "auto-installer-mode.toml absent à la racine de l’ISO"
    return True, ""


def _inject_with_xorriso(
    xorriso: str,
    base_iso: Path,
    out_iso: Path,
    mode_file: Path,
    answer_copy: Path,
) -> tuple[bool, str]:
    ok, reason = _verify_pve_iso_structure(
        xorriso, base_iso, require_autoinstall=False
    )
    if not ok:
        return False, f"ISO source : {reason}"

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
    return _verify_pve_iso_structure(
        xorriso, out_iso, require_autoinstall=True
    )


_MIN_PVE_ISO_BYTES = 200 * 1024 * 1024


def _find_pristine_proxmox_iso(iso_version: IsoVersion, cfg: Settings) -> Path | None:
    """ISO Proxmox d’origine (fichier .iso uploadé), pas le dossier extrait sous boot/."""
    seen: set[Path] = set()
    candidates: list[Path] = []

    def _add(p: Path) -> None:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)

    raw = (iso_version.iso_path or "").strip()
    if raw:
        _add(Path(raw))

    for root in (
        Path(cfg.iso_root),
        Path(cfg.http_root).parent / "isos",
    ):
        pack = root / "proxmox" / str(iso_version.id)
        if pack.is_dir():
            for p in sorted(pack.glob("*.iso")):
                _add(p)

    for p in candidates:
        if p.name in _NETBOOT_ARTIFACT_BASENAMES or p.name.startswith(
            "proxmox-netboot"
        ):
            continue
        try:
            if p.is_file() and p.stat().st_size >= _MIN_PVE_ISO_BYTES:
                return p
        except OSError:
            continue
    return None


def _sync_netboot_isos_from_pristine(netboot_dir: Path, pristine: Path) -> Path:
    """Recopie l’ISO source vers base + manuel (écrase une base corrompue)."""
    netboot_dir.mkdir(parents=True, exist_ok=True)
    base = netboot_base_iso_path(netboot_dir)
    manual = netboot_dir / PROXMOX_NETBOOT_ISO_BASENAME
    shutil.copy2(pristine, base)
    shutil.copy2(pristine, manual)
    logger.info(
        "Proxmox : netboot synchronisé depuis %s → %s + %s",
        pristine.name,
        base.name,
        manual.name,
    )
    return base


def _ensure_base_iso(
    extract_dest: Path,
    iso_version: IsoVersion | None,
    cfg: Settings,
) -> Path:
    """
    Recopie toujours depuis l’ISO Proxmox uploadée avant injection.
    Les ISO netboot vivent dans ``<extract_dest>/netboot/``, pas à la racine extraite.
    """
    if not iso_version:
        raise FileNotFoundError("Version ISO Proxmox requise pour l’injection.")
    netboot_dir = migrate_legacy_proxmox_netboot_isos(extract_dest)
    pristine = _find_pristine_proxmox_iso(iso_version, cfg)
    if not pristine:
        raise FileNotFoundError(
            "ISO Proxmox source introuvable (iso_path ou isos/proxmox/<id>/*.iso) — "
            "vérifiez que l’ISO est encore sur le disque."
        )
    xorriso = shutil.which("xorriso")
    if xorriso:
        ok, reason = _verify_pve_iso_structure(
            xorriso,
            pristine,
            extract_dest=extract_dest,
            require_autoinstall=False,
        )
        if not ok:
            raise RuntimeError(
                f"ISO source invalide ({pristine.name}) : {reason}"
            )
    return _sync_netboot_isos_from_pristine(netboot_dir, pristine)


def inject_proxmox_autoinstall_into_netboot_iso(
    autoinstall_iso: Path,
    answer_toml: Path,
    *,
    boot_dir: Path | None = None,
    iso_version: IsoVersion | None = None,
    settings_obj: Settings | None = None,
) -> None:
    """Crée ou met à jour ``proxmox-netboot-autoinstall.iso`` ; laisse ``proxmox-netboot.iso`` intacte."""
    cfg = settings_obj or settings
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise RuntimeError(
            "xorriso introuvable sur le serveur (apt install xorriso)."
        )

    if boot_dir is not None:
        extract_dest = boot_dir
        if extract_dest.name == PROXMOX_NETBOOT_DIRNAME:
            extract_dest = extract_dest.parent
    else:
        extract_dest = autoinstall_iso.parent
        if extract_dest.name == PROXMOX_NETBOOT_DIRNAME:
            extract_dest = extract_dest.parent
    extract_dest.mkdir(parents=True, exist_ok=True)
    autoinstall_iso = migrate_legacy_proxmox_netboot_isos(extract_dest)
    autoinstall_iso = autoinstall_iso / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME
    base_iso = _ensure_base_iso(extract_dest, iso_version, cfg)

    with tempfile.TemporaryDirectory(prefix="pve-ais-") as tmp:
        tmp_dir = Path(tmp)
        mode_file = tmp_dir / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
        answer_copy = tmp_dir / "answer.toml"
        answer_copy.write_bytes(answer_toml.read_bytes())
        out_iso = tmp_dir / "proxmox-netboot-autoinstall-new.iso"

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

        os.replace(out_iso, autoinstall_iso)

    logger.info(
        "Proxmox autoinstall : %s créé (manuel=%s inchangé, base=%s)",
        autoinstall_iso.name,
        PROXMOX_NETBOOT_ISO_BASENAME,
        base_iso.name,
    )


def inject_active_proxmox_autoinstall(
    iso_version: IsoVersion,
    cfg: AutoConfig,
    *,
    settings_obj: Settings | None = None,
) -> None:
    cfg_settings = settings_obj or settings
    be = iso_version.boot_entry
    answer_p = _answer_toml_path(cfg)
    if not answer_p:
        raise FileNotFoundError(
            f"answer.toml introuvable : {(cfg.file_path or '').strip() or '?'}"
        )
    extract_dest = _proxmox_boot_dir(iso_version, be, cfg_settings)
    if not extract_dest or not _extracted_tree_has_installer(extract_dest):
        raise FileNotFoundError(
            "ISO Proxmox non extraite — extraire l’ISO sur cette version d’abord."
        )
    if not netboot_iso_path(iso_version, be, cfg_settings):
        raise FileNotFoundError(
            "proxmox-netboot.iso absent — ré-extraire l’ISO Proxmox."
        )
    autoinstall = netboot_autoinstall_iso_path(iso_version, be, cfg_settings)
    inject_proxmox_autoinstall_into_netboot_iso(
        autoinstall,
        answer_p,
        boot_dir=extract_dest,
        iso_version=iso_version,
        settings_obj=cfg_settings,
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

    async_result = inject_proxmox_autoinstall_task.delay(version.id, cfg.id, upload.id)
    upload.task_id = async_result.id
    upload.status = "processing"
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload
