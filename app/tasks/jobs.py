"""
Celery background jobs:
  - extract_iso_task   : extracts boot files from an uploaded ISO
  - regenerate_menus_task : regenerates all .ipxe menu files
"""
import logging
from datetime import datetime

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
        paths = extract_iso(version.iso_path, version.os_type.slug, version.id)

        # Upsert BootEntry
        be = version.boot_entry
        if not be:
            be = BootEntry(iso_version_id=version.id)
            db.add(be)

        be.kernel_path = paths.get("kernel_path")
        be.initrd_path = paths.get("initrd_path")
        be.boot_wim_path = paths.get("boot_wim_path")
        be.updated_at = datetime.utcnow()

        version.status = "ready"
        if upload:
            upload.status = "done"
        db.commit()

        # Regenerate menus so this version appears immediately
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)

        return {"status": "ok", "paths": paths}

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
