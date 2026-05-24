"""
Celery background jobs:
  - extract_iso_task      : extracts boot files from an uploaded ISO
  - regenerate_menus_task : regenerates all .ipxe menu files
  - compile_ipxe_task     : compile les firmwares iPXE (undionly.kpxe + ipxe.efi)
"""
import json
import logging
import re
from pathlib import Path
from pathlib import Path
from datetime import datetime, timezone, timedelta
from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.celery_app import celery
from app.database import SessionLocal
from app.models.models import IsoVersion, BootEntry, Upload, AutoConfig

logger = logging.getLogger(__name__)

# Upstream iPXE désactive CONSOLE_CMD / CONSOLE_FRAMEBUFFER sur pcbios (undionly) via #undef ;
# sans ça, « colour », « console » et PNG de fond ne sont pas disponibles en boot BIOS classique.


def _patch_ipxe_graphical_console_headers(src_dir: Path, logs: list[str]) -> None:
    """Retire les #undef qui coupent console / couleur / framebuffer en build BIOS."""
    general = src_dir / "src" / "config" / "general.h"
    console = src_dir / "src" / "config" / "console.h"
    if not general.is_file() or not console.is_file():
        raise RuntimeError(
            f"Sources iPXE incomplètes (config manquante) : {general=} {console=}"
        )

    g = general.read_text(encoding="utf-8", errors="replace")
    g_new = re.sub(r"^[ \t]*#undef CONSOLE_CMD[ \t]*\r?\n", "", g, flags=re.MULTILINE)
    if g_new != g:
        general.write_text(g_new, encoding="utf-8")
        logs.append(
            "config/general.h : retrait de #undef CONSOLE_CMD (console / colour / cpair en BIOS)."
        )

    c = console.read_text(encoding="utf-8", errors="replace")
    c_new = re.sub(
        r"^[ \t]*#undef CONSOLE_FRAMEBUFFER[ \t]*\r?\n", "", c, flags=re.MULTILINE
    )
    if c_new != c:
        console.write_text(c_new, encoding="utf-8")
        logs.append(
            "config/console.h : retrait de #undef CONSOLE_FRAMEBUFFER (fond PNG / mode graphique)."
        )

    if g_new == g and c_new == c:
        logs.append(
            "Aucun #undef CONSOLE_CMD / CONSOLE_FRAMEBUFFER trouvé (déjà patché ou sources inattendues)."
        )


@celery.task(bind=True, name="extract_iso")
def extract_iso_task(self, iso_version_id: int, upload_id: int):
    db = SessionLocal()
    try:
        version: IsoVersion = db.query(IsoVersion).get(iso_version_id)
        upload: Upload = db.query(Upload).get(upload_id)

        if not version:
            raise ValueError(f"IsoVersion {iso_version_id} introuvable")

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
        be.esxi_boot_cfg_legacy_path = None
        be.esxi_efi_boot_path = paths.get("esxi_efi_boot_path")
        be.esxi_modules       = paths.get("esxi_modules") or ""
        be.esxi_modules_legacy = ""
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

        # Regenerate menus so this version appears immediately
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)

        return {"status": "ok", "paths": paths}

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

        label_s = (label or slug).strip() or slug
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
        from app.services.menu_generator import regenerate_all
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
    """
    Clone (ou pull) le dépôt iPXE officiel, patche la config pour console couleur
    et fond PNG en build BIOS, génère embed.ipxe avec un chainload vers menu_url,
    compile undionly.kpxe et les EFI puis copie les binaires dans le TFTP.

    Retourne un dict avec les paths des fichiers produits et les logs.
    """
    import subprocess
    import shutil
    from app.config import settings

    logs: list[str] = []
    completed_steps: list[str] = []
    tftp_dir = Path(settings.tftp_root)
    src_dir  = settings.ipxe_src_dir
    build_dir = Path(settings.build_dir)

    def run(cmd: list[str], cwd=None) -> str:
        """Lance une commande, ajoute sa sortie aux logs, lève en cas d'erreur."""
        logs.append(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd, cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, timeout=1800,
            )
            logs.append(result.stdout[-4000:] if len(result.stdout) > 4000 else result.stdout)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Commande échouée (code {result.returncode}) : {' '.join(cmd)}\n{result.stdout[-2000:]}"
                )
            return result.stdout
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Timeout dépassé pour : {' '.join(cmd)}")

    try:
        self.update_state(
            state="PROGRESS",
            meta={"step": "init", "completed_steps": list(completed_steps), "logs": logs},
        )

        # ── 1. Créer les répertoires nécessaires ────────────────────────────
        build_dir.mkdir(parents=True, exist_ok=True)
        tftp_dir.mkdir(parents=True, exist_ok=True)

        # ── 2. Cloner ou mettre à jour les sources iPXE ─────────────────────
        if (src_dir / ".git").exists():
            logs.append("Sources iPXE déjà présentes — git pull")
            self.update_state(
                state="PROGRESS",
                meta={"step": "git_pull", "completed_steps": list(completed_steps), "logs": logs},
            )
            run(["git", "pull", "--ff-only"], cwd=src_dir)
            completed_steps.append("git_pull")
        else:
            logs.append("Clonage du dépôt iPXE (peut prendre quelques minutes)…")
            self.update_state(
                state="PROGRESS",
                meta={"step": "git_clone", "completed_steps": list(completed_steps), "logs": logs},
            )
            run([
                "git", "clone", "--depth=1",
                "https://github.com/ipxe/ipxe.git",
                str(src_dir),
            ])
            completed_steps.append("git_clone")

        # ── 3. Générer embed.ipxe ────────────────────────────────────────────
        embed_path = src_dir / "src" / "embed.ipxe"
        embed_content = (
            "#!ipxe\n"
            "\n"
            "# Obtenir une IP si pas encore configurée (EFI peut déjà l'avoir fait)\n"
            "isset ${ip} || dhcp || dhcp net0 || dhcp net1\n"
            "\n"
            ":retry\n"
            f"chain --autofree {menu_url} || goto load_error\n"
            "# Menu terminé sans boot (ne doit plus arriver pour « disque local »)\n"
            "exit\n"
            "\n"
            ":load_error\n"
            f"echo iPXE : impossible de charger {menu_url}\n"
            "sleep 5\n"
            "isset ${ip} || dhcp || dhcp net0 || dhcp net1\n"
            "goto retry\n"
        )
        self.update_state(
            state="PROGRESS",
            meta={"step": "embed", "completed_steps": list(completed_steps), "logs": logs},
        )
        embed_path.write_text(embed_content, encoding="utf-8")
        logs.append(f"embed.ipxe généré :\n{embed_content}")
        completed_steps.append("embed")
        make_dir = src_dir / "src"

        # ── 3b. Activer console / couleur / fond PNG sur build BIOS (undionly) ──
        logs.append("Patch config iPXE (CONSOLE_CMD + CONSOLE_FRAMEBUFFER pour pcbios)…")
        self.update_state(
            state="PROGRESS",
            meta={"step": "patch_ipxe_config", "completed_steps": list(completed_steps), "logs": logs},
        )
        _patch_ipxe_graphical_console_headers(src_dir, logs)
        completed_steps.append("patch_ipxe_config")

        # ── 4. Compiler undionly.kpxe (BIOS) ────────────────────────────────
        logs.append("Compilation undionly.kpxe (BIOS)…")
        self.update_state(
            state="PROGRESS",
            meta={"step": "compile_bios", "completed_steps": list(completed_steps), "logs": logs},
        )
        run(["make", "bin/undionly.kpxe", "EMBED=embed.ipxe"], cwd=make_dir)
        completed_steps.append("compile_bios")

        # ── 5a. Compiler snponly.efi (UEFI — utilise drivers réseau EFI de la VM) ─
        # snponly.efi délègue au SNP (Simple Network Protocol) de l'EFI :
        # compatible virtio-net, e1000, vmxnet3, etc. sans driver intégré.
        logs.append("Compilation snponly.efi (UEFI SNP — VMs / matériel avec driver EFI)…")
        self.update_state(
            state="PROGRESS",
            meta={"step": "compile_efi", "completed_steps": list(completed_steps), "logs": logs},
        )
        run(["make", "bin-x86_64-efi/snponly.efi", "EMBED=embed.ipxe"], cwd=make_dir)

        # ── 5b. Compiler ipxe.efi (UEFI — drivers NIC intégrés, physique/bare-metal) ─
        logs.append("Compilation ipxe.efi (UEFI drivers intégrés — bare-metal)…")
        run(["make", "bin-x86_64-efi/ipxe.efi", "EMBED=embed.ipxe"], cwd=make_dir)
        completed_steps.append("compile_efi")

        # ── 6. Copier les binaires en TFTP ───────────────────────────────────
        logs.append(f"Copie des binaires vers {tftp_dir}")
        self.update_state(
            state="PROGRESS",
            meta={"step": "copy", "completed_steps": list(completed_steps), "logs": logs},
        )

        kpxe_src     = make_dir / "bin" / "undionly.kpxe"
        efi_src      = make_dir / "bin-x86_64-efi" / "ipxe.efi"
        snponly_src  = make_dir / "bin-x86_64-efi" / "snponly.efi"

        shutil.copy2(kpxe_src,    tftp_dir / "undionly.kpxe")
        shutil.copy2(efi_src,     tftp_dir / "ipxe.efi")
        shutil.copy2(snponly_src, tftp_dir / "snponly.efi")

        for fname in ("undionly.kpxe", "ipxe.efi", "snponly.efi"):
            (tftp_dir / fname).chmod(0o644)

        completed_steps.append("copy")
        logs.append("Compilation terminée avec succès.")
        return {
            "status":          "success",
            "menu_url":        menu_url,
            "embed":           embed_content,
            "undionly":        str(tftp_dir / "undionly.kpxe"),
            "efi":             str(tftp_dir / "ipxe.efi"),
            "snponly":         str(tftp_dir / "snponly.efi"),
            "logs":            "\n".join(logs),
            "completed_steps": completed_steps,
        }

    except SoftTimeLimitExceeded:
        logs.append("TIMEOUT : compilation annulée après 40 minutes.")
        raise
    except Exception as exc:
        logs.append(f"ERREUR : {exc}")
        logger.exception("compile_ipxe_task a échoué")
        raise
