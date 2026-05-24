"""
Proxmox VE — answer.toml et injection dans proxmox-netboot.iso.

- Config : ``configs/proxmox/…/answer.toml`` + copie ``boot/proxmox/<version>/answer.toml``.
- ISO autoinstall : copie de ``proxmox-netboot.iso`` + ``answer.toml`` et ``auto-installer-mode.toml``
  à la racine (assistant Proxmox ou xorriso ; repli HTTP si xorriso n’embarque pas l’answer).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import resolve_server_base_url, settings
from app.models.models import AutoConfig, IsoVersion, Upload
from app.services.autoconfig_publish import (
    PROXMOX_ANSWER_BASENAME,
    proxmox_boot_version_dir,
    proxmox_boot_version_segment,
    publish_proxmox_answer_from_autoconfig,
)
from app.services.iso_extractor import (
    PROXMOX_NETBOOT_AUTOINSTALL_BASENAME,
    PROXMOX_NETBOOT_ISO_BASENAME,
    migrate_legacy_proxmox_netboot_isos,
)

logger = logging.getLogger(__name__)

_AUTOINSTALLER_MODE_ISO = 'mode = "iso"\n'


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


def _answer_http_url(iso_version: IsoVersion) -> str:
    seg = proxmox_boot_version_segment(iso_version)
    rel = f"boot/proxmox/{seg}/{PROXMOX_ANSWER_BASENAME}"
    return f"{resolve_server_base_url().rstrip('/')}/{rel}"


def _mode_http_toml(url: str) -> str:
    return (
        'mode = "http"\n'
        'partition_label = "proxmox-ais"\n'
        "\n"
        "[http]\n"
        f'url = "{url}"\n'
    )


def _xorriso_ok(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode in (0, 1, 5, 32)


def _iso_has_root_file(xorriso: str, iso: Path, basename: str) -> bool:
    """Détecte un fichier à la racine ISO (-find, puis extraction osirrox)."""
    for name in (basename, basename.upper()):
        proc = subprocess.run(
            [xorriso, "-indev", str(iso), "-find", "/", "-name", name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if _xorriso_ok(proc) and name.lower() in (proc.stdout or "").lower():
            return True

    with tempfile.TemporaryDirectory(prefix="pve-ls-") as tmp:
        dest = Path(tmp)
        for iso_path in (f"/{basename}", f"/{basename.upper()}"):
            subprocess.run(
                [
                    xorriso,
                    "-osirrox",
                    "on",
                    "-indev",
                    str(iso),
                    "-extract",
                    iso_path,
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if (dest / basename).is_file():
                return True
            for hit in dest.rglob(basename):
                if hit.is_file():
                    return True
            for hit in dest.iterdir():
                if hit.is_file() and hit.name.lower() == basename.lower():
                    return True
    return False


def _verify_iso_size(iso: Path, src_size: int) -> None:
    if iso.stat().st_size < src_size * 0.9:
        raise RuntimeError(
            f"ISO autoinstall tronquée ({iso.stat().st_size} o, source {src_size} o)"
        )


def _verify_embedded_iso(xorriso: str, iso: Path, src_size: int) -> None:
    _verify_iso_size(iso, src_size)
    if not _iso_has_root_file(xorriso, iso, "answer.toml"):
        raise RuntimeError("answer.toml introuvable dans l’ISO après injection")
    if not _iso_has_root_file(xorriso, iso, "auto-installer-mode.toml"):
        raise RuntimeError("auto-installer-mode.toml introuvable dans l’ISO")


def _verify_http_iso(xorriso: str, iso: Path, src_size: int) -> None:
    _verify_iso_size(iso, src_size)
    if not _iso_has_root_file(xorriso, iso, "auto-installer-mode.toml"):
        raise RuntimeError("auto-installer-mode.toml introuvable dans l’ISO")


def _xorriso_map_files(
    xorriso: str,
    netboot_iso: Path,
    out_iso: Path,
    maps: list[tuple[Path, str]],
) -> None:
    """
    Mode « modifying » : indev ≠ outdev, outdev = fichier neuf (pas de copie cp avant).
    """
    out_iso.parent.mkdir(parents=True, exist_ok=True)
    if out_iso.exists():
        out_iso.unlink()

    cmd = [
        xorriso,
        "-indev",
        str(netboot_iso),
        "-outdev",
        str(out_iso),
        "-boot_image",
        "any",
        "replay",
    ]
    for local_path, iso_path in maps:
        cmd.extend(["-map", str(local_path), iso_path])
    cmd.append("-commit")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if not _xorriso_ok(proc):
        blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise RuntimeError(
            f"Échec xorriso : {blob[-2000:] if blob else proc.returncode}"
        )


def _inject_with_proxmox_assistant(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> bool:
    assistant = shutil.which("proxmox-auto-install-assistant")
    if not assistant:
        return False
    tmp_out = out_iso.with_name(f".{out_iso.name}.assistant")
    try:
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
        if proc.returncode != 0 or not tmp_out.is_file():
            blob = ((proc.stderr or "") + (proc.stdout or "")).strip()
            logger.warning(
                "proxmox-auto-install-assistant : %s",
                blob[-2000:] if blob else proc.returncode,
            )
            tmp_out.unlink(missing_ok=True)
            return False
        os.replace(tmp_out, out_iso)
        logger.info("Proxmox : %s via proxmox-auto-install-assistant", out_iso.name)
        return True
    except Exception:
        logger.exception("proxmox-auto-install-assistant")
        tmp_out.unlink(missing_ok=True)
        return False


def _inject_embedded_xorriso(
    xorriso: str,
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="pve-inject-") as tmp:
        mode_file = Path(tmp) / "auto-installer-mode.toml"
        mode_file.write_text(_AUTOINSTALLER_MODE_ISO, encoding="utf-8")
        _xorriso_map_files(
            xorriso,
            netboot_iso,
            out_iso,
            [
                (mode_file, "/auto-installer-mode.toml"),
                (answer_toml, "/answer.toml"),
            ],
        )


def _inject_http_xorriso(
    xorriso: str,
    netboot_iso: Path,
    answer_url: str,
    out_iso: Path,
) -> None:
    """answer.toml reste sur HTTP (boot/…) ; seul auto-installer-mode.toml est dans l’ISO."""
    with tempfile.TemporaryDirectory(prefix="pve-inject-") as tmp:
        mode_file = Path(tmp) / "auto-installer-mode.toml"
        mode_file.write_text(_mode_http_toml(answer_url), encoding="utf-8")
        _xorriso_map_files(
            xorriso,
            netboot_iso,
            out_iso,
            [(mode_file, "/auto-installer-mode.toml")],
        )


def inject_answer_into_netboot_iso(
    netboot_iso: Path,
    answer_toml: Path,
    out_iso: Path,
    *,
    iso_version: IsoVersion | None = None,
) -> None:
    if not netboot_iso.is_file():
        raise FileNotFoundError(f"proxmox-netboot.iso absent : {netboot_iso}")
    if not answer_toml.is_file():
        raise FileNotFoundError(f"answer.toml absent : {answer_toml}")

    out_iso.parent.mkdir(parents=True, exist_ok=True)
    src_size = netboot_iso.stat().st_size
    xorriso = shutil.which("xorriso")
    errors: list[str] = []

    if _inject_with_proxmox_assistant(netboot_iso, answer_toml, out_iso):
        if xorriso:
            try:
                _verify_embedded_iso(xorriso, out_iso, src_size)
                return
            except RuntimeError as exc:
                errors.append(f"assistant+verify: {exc}")
                out_iso.unlink(missing_ok=True)
        else:
            return

    if not xorriso:
        raise RuntimeError(
            "xorriso introuvable — apt install xorriso "
            "(recommandé : proxmox-auto-install-assistant)"
        )

    tmp_out = out_iso.with_name(f".{out_iso.name}.injecting")
    try:
        if tmp_out.exists():
            tmp_out.unlink()
        _inject_embedded_xorriso(xorriso, netboot_iso, answer_toml, tmp_out)
        _verify_embedded_iso(xorriso, tmp_out, src_size)
        os.replace(tmp_out, out_iso)
        logger.info(
            "Proxmox : %s (answer embarqué, xorriso) — %s o",
            out_iso.name,
            out_iso.stat().st_size,
        )
        return
    except Exception as exc:
        errors.append(f"xorriso/iso: {exc}")
        tmp_out.unlink(missing_ok=True)

    if iso_version is None:
        raise RuntimeError(
            "Injection ISO impossible : " + " | ".join(errors)
        )

    answer_url = _answer_http_url(iso_version)
    try:
        if tmp_out.exists():
            tmp_out.unlink()
        _inject_http_xorriso(xorriso, netboot_iso, answer_url, tmp_out)
        _verify_http_iso(xorriso, tmp_out, src_size)
        os.replace(tmp_out, out_iso)
        logger.info(
            "Proxmox : %s (fetch HTTP %s) — %s o",
            out_iso.name,
            answer_url,
            out_iso.stat().st_size,
        )
        return
    except Exception as exc:
        errors.append(f"xorriso/http: {exc}")
        tmp_out.unlink(missing_ok=True)
        raise RuntimeError(
            "Injection dans proxmox-netboot-autoinstall.iso impossible. "
            + " | ".join(errors)
            + f" — installez proxmox-auto-install-assistant ou vérifiez que {answer_url} est joignable."
        ) from exc


def inject_active_proxmox_autoinstall(
    iso_version: IsoVersion,
    cfg: AutoConfig,
) -> None:
    netboot = netboot_iso_path(iso_version)
    if not netboot:
        raise FileNotFoundError(
            "proxmox-netboot.iso absent — extraire l’ISO Proxmox sur cette version."
        )
    answer = _resolve_answer_toml(iso_version, cfg)
    out = netboot_autoinstall_iso_path(iso_version)
    inject_answer_into_netboot_iso(
        netboot, answer, out, iso_version=iso_version
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
