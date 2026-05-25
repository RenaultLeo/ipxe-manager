"""
Gestion des tâches Celery en cours : liste, kill individuel, kill-all.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import auth_redirect_admin, auth_redirect_login, get_session_user
from app.services.ownership import get_upload
from app.models.models import Upload, IsoVersion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs")


def _auth(request: Request):
    return auth_redirect_login(request)


def _revoke(task_id: str):
    """Révoquer une tâche Celery par son task_id (SIGKILL)."""
    if not task_id:
        return
    try:
        from app.tasks.celery_app import celery
        celery.control.revoke(task_id, terminate=True, signal="SIGKILL")
        logger.info("Tâche Celery révoquée : %s", task_id)
    except Exception:
        logger.exception("Impossible de révoquer la tâche %s", task_id)


def _resolve_version_for_upload(upload: Upload, db: Session) -> IsoVersion | None:
    """Retrouve la version ISO liée à un job d'extraction (upload)."""
    if upload.iso_version_id:
        return db.query(IsoVersion).filter(IsoVersion.id == upload.iso_version_id).first()
    if upload.file_type != "extraction":
        return None
    fname = (upload.filename or "").strip()
    extracting = db.query(IsoVersion).filter(IsoVersion.status == "extracting").all()
    if not extracting:
        return None
    if fname:
        for v in extracting:
            if v.iso_path and Path(v.iso_path).name == fname:
                return v
    if len(extracting) == 1:
        return extracting[0]
    return None


def _mark_error(upload: Upload, db: Session, msg: str = "Annulé manuellement"):
    upload.status = "error"
    upload.error_msg = msg
    version = _resolve_version_for_upload(upload, db)
    if version and version.status in ("extracting", "uploaded"):
        version.status = "error"
        logger.info("Version %s marquée en erreur (job %s annulé)", version.id, upload.id)


@router.post("/{upload_id}/kill")
async def kill_job(upload_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    user = get_session_user(request)
    upload = get_upload(db, user, upload_id)
    if not upload:
        return RedirectResponse("/?killed=notfound", status_code=302)

    _revoke(upload.task_id)
    _mark_error(upload, db)
    db.commit()

    return RedirectResponse("/?killed=1", status_code=302)


@router.get("/kill-all")
async def kill_all_get(request: Request):
    """Redirect GET to dashboard (the form uses POST)."""
    return RedirectResponse("/", status_code=302)


@router.post("/kill-all")
async def kill_all_jobs(request: Request, db: Session = Depends(get_db)):
    redir = auth_redirect_admin(request)
    if redir:
        return redir

    active = (
        db.query(Upload)
        .filter(Upload.status.in_(["pending", "processing"]))
        .all()
    )
    count = 0
    for upload in active:
        _revoke(upload.task_id)
        _mark_error(upload, db)
        count += 1

    # Versions encore en extracting sans upload actif (sécurité)
    stuck_versions = db.query(IsoVersion).filter(IsoVersion.status == "extracting").all()
    for v in stuck_versions:
        v.status = "error"
        logger.info("Version %s marquée en erreur (kill-all)", v.id)

    db.commit()
    logger.info(
        "kill-all : %d upload(s) annulé(s), %d version(s) en erreur",
        count,
        len(stuck_versions),
    )

    return RedirectResponse("/?killed=all", status_code=302)
