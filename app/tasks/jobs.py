"""
Celery background jobs:
  - extract_iso_task      : extracts boot files from an uploaded ISO
  - regenerate_menus_task : regenerates all .ipxe menu files
  - compile_ipxe_task     : compile les firmwares iPXE (undionly.kpxe + ipxe.efi)
"""
import logging
from datetime import datetime, timezone, timedelta
from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.celery_app import celery
from app.database import SessionLocal
from app.models.models import IsoVersion, BootEntry, Upload

logger = logging.getLogger(__name__)


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
        )

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
        be.modloop_path  = paths.get("modloop_path")
        be.updated_at    = datetime.utcnow()

        version.status = "ready"
        if upload:
            upload.status = "done"
        db.commit()

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
    Clone (ou pull) le dépôt iPXE officiel, génère embed.ipxe avec un
    chainload vers menu_url, compile undionly.kpxe et ipxe.efi puis
    copie les binaires dans le répertoire TFTP.

    Retourne un dict avec les paths des fichiers produits et les logs.
    """
    import subprocess
    import shutil
    from pathlib import Path
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
            f"chain --autofree {menu_url} ||\n"
            f"  echo iPXE : impossible de charger {menu_url} &&\n"
            "  sleep 5 &&\n"
            "  goto start\n"
            "\n"
            ":start\n"
            "isset ${ip} || dhcp || dhcp net0 || dhcp net1\n"
            f"chain --autofree {menu_url}\n"
        )
        self.update_state(
            state="PROGRESS",
            meta={"step": "embed", "completed_steps": list(completed_steps), "logs": logs},
        )
        embed_path.write_text(embed_content, encoding="utf-8")
        logs.append(f"embed.ipxe généré :\n{embed_content}")
        completed_steps.append("embed")
        make_dir = src_dir / "src"

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
