"""
Celery background jobs:
  - extract_iso_task      : extracts boot files from an uploaded ISO
  - regenerate_menus_task : regenerates all .ipxe menu files
  - compile_ipxe_task     : compile les firmwares iPXE (undionly.kpxe + ipxe.efi)
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from celery.exceptions import SoftTimeLimitExceeded

from sqlalchemy.orm import joinedload

from app.tasks.celery_app import celery
from app.database import SessionLocal
from app.models.models import IsoVersion, BootEntry, Upload, AutoConfig

logger = logging.getLogger(__name__)

# Patches iPXE : voir app.services.ipxe_compiler (HTTPS ré-appliqué après chaque git pull).


@celery.task(bind=True, name="extract_iso")
def extract_iso_task(self, iso_version_id: int, upload_id: int):
    db = SessionLocal()
    try:
        version: IsoVersion | None = (
            db.query(IsoVersion)
            .options(joinedload(IsoVersion.os_type))
            .get(iso_version_id)
        )
        upload: Upload | None = db.query(Upload).get(upload_id)

        if not version:
            raise ValueError(f"IsoVersion {iso_version_id} introuvable")
        if not version.os_type:
            raise ValueError(f"IsoVersion {iso_version_id} : type d'OS introuvable")

        version.status = "extracting"
        if upload:
            upload.status = "processing"
            upload.task_id = self.request.id
        db.commit()

        from app.services.iso_extractor import extract_iso
        paths = extract_iso(
            version.iso_path,
            version.os_type.slug,
            version.id,
            version.version_label,
            os_type=version.os_type,
        )

        meta = paths.pop("_meta", None)
        extra_linux = paths.pop("extra_linux_paths", None)

        br = {}
        if isinstance(meta, dict):
            raw_br = meta.get("basename_report")
            if isinstance(raw_br, dict):
                br = raw_br
        version.extract_basename_report_json = json.dumps(br, ensure_ascii=False) if br else ""

        # Upsert BootEntry
        be = version.boot_entry
        if not be:
            be = BootEntry(iso_version_id=version.id)
            db.add(be)

        be.kernel_path   = paths.get("kernel_path")
        be.initrd_path   = paths.get("initrd_path")
        be.boot_wim_path = paths.get("boot_wim_path")
        be.bcd_path      = paths.get("bcd_path")
        be.boot_sdi_path = paths.get("boot_sdi_path")
        be.bootmgr_path  = paths.get("bootmgr_path")

        if (version.os_type.boot_type or "").lower() == "windows":
            from app.services.windows_boot_paths import (
                sync_windows_boot_entry_from_disk,
                version_slug_for_disk,
            )

            sync_windows_boot_entry_from_disk(
                be,
                version.os_type.slug,
                version_slug_for_disk(be, version.version_label, version.id),
            )
        be.modloop_path  = paths.get("modloop_path")
        be.esxi_boot_cfg_path = paths.get("esxi_boot_cfg_path")
        be.esxi_boot_cfg_manual_path = paths.get("esxi_boot_cfg_manual_path")
        be.esxi_efi_boot_path = paths.get("esxi_efi_boot_path")
        be.esxi_modules       = paths.get("esxi_modules") or ""
        if extra_linux is not None:
            if isinstance(extra_linux, list):
                be.extra_linux_paths_json = json.dumps(extra_linux, ensure_ascii=False)
            else:
                be.extra_linux_paths_json = "[]"
        be.updated_at    = datetime.utcnow()

        version.status = "ready"
        version.iso_was_extracted = True
        if upload:
            upload.status = "done"
            upload.error_msg = ""

        if getattr(version, "delete_iso_after_next_extract", False) and version.iso_path:
            iso_to_remove = Path(version.iso_path)
            removed_ok = False
            if iso_to_remove.is_file():
                try:
                    iso_to_remove.unlink(missing_ok=True)
                    removed_ok = True
                except OSError as ex:
                    logger.warning(
                        "delete_iso_after_next_extract : suppression impossible pour %s (%s)",
                        iso_to_remove,
                        ex,
                    )
            if removed_ok:
                version.iso_path = ""
                version.iso_size = 0
                parent = iso_to_remove.parent
                try:
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
            version.delete_iso_after_next_extract = False

        db.commit()

        if getattr(version, "active_autoconfig_id", None) and version.os_type.slug == "ubuntu":
            ac = db.query(AutoConfig).get(version.active_autoconfig_id)
            if ac:
                try:
                    from app.services.autoconfig_publish import publish_ubuntu_cloud_config

                    publish_ubuntu_cloud_config(version, ac)
                except Exception:
                    logger.exception(
                        "Republication config courante après extraction (version %s)",
                        version.id,
                    )

        if getattr(version, "active_autoconfig_id", None) and version.os_type.slug == "proxmox":
            ac = db.query(AutoConfig).get(version.active_autoconfig_id)
            if ac and ac.config_type == "proxmox-answer":
                try:
                    from app.services.proxmox_autoinstall import (
                        inject_active_proxmox_autoinstall,
                    )

                    inject_active_proxmox_autoinstall(version, ac)
                except Exception:
                    logger.exception(
                        "Ré-injection autoinstall après extraction (version %s)",
                        version.id,
                    )

        # Regenerate menus so this version appears immediately
        from app.config import settings
        from app.services.filesystem_perms import prepare_menus_dir
        from app.services.menu_generator import regenerate_all

        menu_warning: str | None = None
        prepare_menus_dir(settings.menus_dir)
        try:
            regenerate_all(db)
        except Exception as menu_exc:
            logger.exception(
                "Régénération menus après extraction (version %s)", iso_version_id
            )
            prepare_menus_dir(settings.menus_dir)
            try:
                regenerate_all(db)
            except Exception as retry_exc:
                menu_warning = str(retry_exc)
                logger.exception(
                    "Régénération menus (2e tentative) échouée pour version %s",
                    iso_version_id,
                )

        if menu_warning and upload_id:
            upl = db.query(Upload).get(upload_id)
            if upl:
                upl.error_msg = (
                    "Extraction réussie, mais régénération des menus iPXE échouée : "
                    + menu_warning
                )[:4000]
                db.commit()

        result: dict = {"status": "ok", "paths": paths}
        if menu_warning:
            result["menu_warning"] = menu_warning
        return result

    except SoftTimeLimitExceeded:
        logger.warning("extract_iso_task timeout (SoftTimeLimitExceeded) pour version %s", iso_version_id)
        db.rollback()
        try:
            version = db.query(IsoVersion).get(iso_version_id)
            if version:
                version.status = "error"
            upload = db.query(Upload).get(upload_id)
            if upload:
                upload.status = "error"
                upload.error_msg = "Timeout : extraction trop longue, tâche annulée"
            db.commit()
        except Exception:
            pass
        raise

    except Exception as exc:
        logger.exception("extract_iso_task failed")
        db.rollback()
        try:
            version = db.query(IsoVersion).get(iso_version_id)
            if version:
                version.status = "error"
            upload = db.query(Upload).get(upload_id)
            if upload:
                upload.status = "error"
                upload.error_msg = str(exc)
            db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=0, max_retries=0)
    finally:
        db.close()


@celery.task(bind=True, name="upload_winpe_drivers_zip", soft_time_limit=3600, time_limit=3900)
def upload_winpe_drivers_zip_task(
    self,
    upload_id: int,
    label: str,
    slug: str,
    part_path: str,
):
    """Dézippe une archive pilotes dans boot/drivers/<slug>/ et met à jour drivers.json."""
    from app.services.winpe_drivers import (
        process_driver_zip_upload,
        staging_zip_part_path,
    )

    db = SessionLocal()
    part = Path(part_path)
    try:
        upload: Upload | None = db.query(Upload).get(upload_id)
        if not upload:
            raise ValueError(f"Upload {upload_id} introuvable")

        upload.status = "processing"
        upload.task_id = self.request.id
        db.commit()

        if not part.is_file():
            raise FileNotFoundError(f"Archive ZIP temporaire absente : {part}")

        result = process_driver_zip_upload(
            zip_path=part,
            label=label,
            slug=slug,
        )

        upload.status = "done"
        upload.size = part.stat().st_size
        upload.filename = f"{label}|{slug}|{part.name}"
        upload.error_msg = json.dumps(
            {
                "extracted_files": result["extracted_files"],
                "inf_count": result["inf_count"],
                "path": result["path"],
            },
            ensure_ascii=False,
        )
        db.commit()
        logger.info(
            "Pilotes ZIP OK — upload %s profil %s (%s .inf, %s fichiers)",
            upload_id,
            label,
            result["inf_count"],
            result["extracted_files"],
        )
        return result
    except Exception as exc:
        logger.exception("upload_winpe_drivers_zip_task failed (upload %s)", upload_id)
        db.rollback()
        try:
            upload = db.query(Upload).get(upload_id)
            if upload:
                upload.status = "error"
                upload.error_msg = str(exc)[:4000]
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=0, max_retries=0)
    finally:
        try:
            if part.is_file():
                part.unlink()
            alt = staging_zip_part_path(upload_id)
            if alt.is_file() and alt != part:
                alt.unlink()
        except OSError:
            pass
        db.close()


@celery.task(bind=True, name="upload_winpe_install", soft_time_limit=7200, time_limit=7500)
def upload_winpe_install_task(
    self,
    upload_id: int,
    iso_version_id: int,
    slug: str,
    label: str,
    wim_index: int,
    part_path: str,
):
    """
    Finalise un install.wim reçu en HTTP (fichier .part) : déplacement atomique,
    enregistrement WinpeInstall, mise à jour du journal Upload.
    """
    import os
    from sqlalchemy.orm import joinedload

    from app.models.models import WinpeInstall
    from app.services.winpe_installs import INSTALL_WIM_FILENAME, install_wim_path

    db = SessionLocal()
    part = Path(part_path)
    try:
        upload: Upload | None = db.query(Upload).get(upload_id)
        if not upload:
            raise ValueError(f"Upload {upload_id} introuvable")

        upload.status = "processing"
        upload.task_id = self.request.id
        db.commit()

        version = (
            db.query(IsoVersion)
            .options(joinedload(IsoVersion.os_type))
            .filter(IsoVersion.id == iso_version_id)
            .first()
        )
        if not version:
            raise ValueError(f"IsoVersion {iso_version_id} introuvable")

        if not part.is_file():
            raise FileNotFoundError(f"Fichier temporaire absent : {part}")

        dest = install_wim_path(version, slug)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            dest.unlink()
        os.replace(part, dest)

        label_s = " ".join((label or slug).replace("\r", " ").replace("\n", " ").replace("\t", " ").split()) or slug
        row = (
            db.query(WinpeInstall)
            .filter(
                WinpeInstall.iso_version_id == version.id,
                WinpeInstall.slug == slug,
            )
            .first()
        )
        if not row:
            row = WinpeInstall(
                iso_version_id=version.id,
                slug=slug,
                label=label_s,
                wim_index=max(1, int(wim_index or 1)),
            )
            db.add(row)
        else:
            row.label = label_s
            row.wim_index = max(1, int(wim_index or 1))

        version.winpe_startnet_patched_at = None
        upload.status = "done"
        upload.size = dest.stat().st_size
        upload.filename = f"{slug}/{INSTALL_WIM_FILENAME}"
        db.commit()
        try:
            regenerate_winpe_scripts_task.delay(iso_version_id)
        except Exception:
            logger.warning(
                "Impossible de lancer regenerate_winpe_scripts après upload install.wim",
                exc_info=True,
            )
        logger.info(
            "install.wim WinPE OK — version %s slug %s (%s octets)",
            iso_version_id,
            slug,
            upload.size,
        )
        return {"status": "ok", "slug": slug, "path": str(dest)}
    except Exception as exc:
        logger.exception("upload_winpe_install_task failed")
        db.rollback()
        try:
            upload = db.query(Upload).get(upload_id)
            if upload:
                upload.status = "error"
                upload.error_msg = str(exc)[:4000]
                db.commit()
        except Exception:
            pass
        try:
            if part.is_file():
                part.unlink()
        except OSError:
            pass
        raise self.retry(exc=exc, countdown=0, max_retries=0)
    finally:
        db.close()


def _regenerate_winpe_scripts_impl(iso_version_id: int) -> dict:
    """Génère deploy.ps1 / inject-drivers.ps1 / masters.json et injecte startnet.cmd minimal."""
    db = SessionLocal()
    try:
        from app.models.models import IsoVersion, WinpeInstall
        from app.services.winpe_scripts import regenerate_winpe_deployment
        from sqlalchemy.orm import joinedload

        version = (
            db.query(IsoVersion)
            .options(
                joinedload(IsoVersion.os_type),
                joinedload(IsoVersion.boot_entry),
                joinedload(IsoVersion.winpe_installs),
            )
            .filter(IsoVersion.id == iso_version_id)
            .first()
        )
        if not version:
            raise ValueError(f"IsoVersion {iso_version_id} introuvable")

        if version.boot_entry and (version.os_type.boot_type or "").lower() == "windows":
            from app.services.windows_boot_paths import (
                sync_windows_boot_entry_from_disk,
                version_slug_for_disk,
            )

            be = version.boot_entry
            seg = version_slug_for_disk(be, version.version_label, version.id)
            sync_windows_boot_entry_from_disk(be, version.os_type.slug, seg)
            db.flush()

        installs = list(version.winpe_installs or [])
        if not installs:
            installs = (
                db.query(WinpeInstall)
                .filter(WinpeInstall.iso_version_id == version.id)
                .all()
            )
        result = regenerate_winpe_deployment(version, installs, patch_wim=True)
        version.winpe_startnet_patched_at = datetime.utcnow()
        db.commit()
        logger.info(
            "regenerate_winpe_scripts OK — version %s (%s master(s))",
            iso_version_id,
            result.get("masters", 0),
        )
        return {"status": "ok", **result}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery.task(bind=True, name="inject_proxmox_autoinstall", soft_time_limit=900, time_limit=1200)
def inject_proxmox_autoinstall_task(
    self,
    iso_version_id: int,
    config_id: int,
    upload_id: int,
):
    """Injecte answer.toml dans proxmox-netboot-autoinstall.iso puis régénère les menus iPXE."""
    from sqlalchemy.orm import joinedload

    from app.database import update_upload_status
    from app.services.menu_generator import regenerate_all
    from app.services.proxmox_autoinstall import inject_active_proxmox_autoinstall

    update_upload_status(
        upload_id, "processing", task_id=self.request.id
    )

    db = SessionLocal()
    try:
        version = (
            db.query(IsoVersion)
            .options(
                joinedload(IsoVersion.boot_entry),
                joinedload(IsoVersion.os_type),
            )
            .filter(IsoVersion.id == iso_version_id)
            .first()
        )
        cfg = db.query(AutoConfig).filter(AutoConfig.id == config_id).first()
        if not version or not cfg:
            raise ValueError(
                f"Version {iso_version_id} ou config {config_id} introuvable"
            )
        # Charger les relations avant de fermer la session (prepare-iso = long).
        _ = version.boot_entry
        _ = version.os_type
    finally:
        db.close()

    try:
        inject_active_proxmox_autoinstall(version, cfg)
    except Exception as exc:
        logger.exception("inject_proxmox_autoinstall_task failed")
        try:
            update_upload_status(upload_id, "error", error_msg=str(exc))
        except Exception:
            logger.exception("Impossible d'enregistrer l'erreur upload %s", upload_id)
        raise self.retry(exc=exc, countdown=0, max_retries=0)

    db = SessionLocal()
    try:
        regenerate_all(db)
        db.commit()
    finally:
        db.close()

    update_upload_status(upload_id, "done")
    logger.info(
        "Proxmox autoinstall injecté — version %s config %s",
        iso_version_id,
        config_id,
    )
    return {"status": "ok", "iso_version_id": iso_version_id, "config_id": config_id}


@celery.task(bind=True, name="regenerate_winpe_scripts", soft_time_limit=900, time_limit=1200)
def regenerate_winpe_scripts_task(self, iso_version_id: int):
    try:
        return _regenerate_winpe_scripts_impl(iso_version_id)
    except Exception as exc:
        logger.exception(
            "regenerate_winpe_scripts_task failed (version %s) : %s",
            iso_version_id,
            exc,
        )
        raise


@celery.task(bind=True, name="patch_winpe_startnet", soft_time_limit=900, time_limit=1200)
def patch_winpe_startnet_task(
    self, iso_version_id: int, winpe_install_id: int | None = None
):
    """Alias historique (winpe_install_id ignoré)."""
    return _regenerate_winpe_scripts_impl(iso_version_id)


@celery.task(name="regenerate_menus")
def regenerate_menus_task():
    db = SessionLocal()
    try:
        from app.config import settings
        from app.services.filesystem_perms import prepare_menus_dir
        from app.services.menu_generator import regenerate_all

        prepare_menus_dir(settings.menus_dir)
        written = regenerate_all(db)
        return {"written": written}
    finally:
        db.close()


@celery.task(
    bind=True,
    name="compile_ipxe",
    soft_time_limit=2400,   # 40 min
    time_limit=2700,        # 45 min hard
)
def compile_ipxe_task(self, menu_url: str):
    """Compile les firmwares iPXE (HTTPS + TRUST CA locale si présente)."""
    from app.services.ipxe_compiler import compile_ipxe_firmware

    logs: list[str] = []
    completed_steps: list[str] = []

    def on_progress(step: str, completed: list[str], step_logs: list[str]) -> None:
        nonlocal logs, completed_steps
        logs = step_logs
        completed_steps = completed
        self.update_state(
            state="PROGRESS",
            meta={
                "step": step,
                "completed_steps": list(completed_steps),
                "logs": logs,
            },
        )

    try:
        return compile_ipxe_firmware(menu_url, on_progress=on_progress)
    except SoftTimeLimitExceeded:
        logs.append("TIMEOUT : compilation annulée après 40 minutes.")
        raise
    except Exception as exc:
        logs.append(f"ERREUR : {exc}")
        logger.exception("compile_ipxe_task a échoué")
        raise
