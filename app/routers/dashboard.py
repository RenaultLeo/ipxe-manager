from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.services.disk_info import get_disk_usage, fmt_size
from app.models.models import OsType, IsoVersion, Upload
from app.services.os_type_order import sort_os_types_for_ui
from app.config import settings
from app.templating import templates, template_context

router = APIRouter()


def _auth_redirect(request: Request):
    from fastapi.responses import RedirectResponse
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    redir = _auth_redirect(request)
    if redir:
        return redir

    try:
        disk = get_disk_usage()
        os_types = sort_os_types_for_ui(db.query(OsType).all())

        stats = []
        for ot in os_types:
            total = db.query(IsoVersion).filter(IsoVersion.os_type_id == ot.id).count()
            ready = db.query(IsoVersion).filter(
                IsoVersion.os_type_id == ot.id, IsoVersion.status == "ready"
            ).count()
            stats.append({"os": ot, "total": total, "ready": ready})

        recent_uploads = (
            db.query(Upload).order_by(Upload.created_at.desc()).limit(10).all()
        )
        active_jobs_list = (
            db.query(Upload)
            .filter(Upload.status.in_(["pending", "processing"]))
            .order_by(Upload.created_at.desc())
            .all()
        )
        active_jobs = len(active_jobs_list)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        disk = {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}
        stats, recent_uploads, active_jobs, active_jobs_list = [], [], 0, []

    return templates.TemplateResponse(
        "dashboard.html",
        template_context(
            request,
            disk=disk,
            fmt_size=fmt_size,
            stats=stats,
            recent_uploads=recent_uploads,
            active_jobs=active_jobs,
            active_jobs_list=active_jobs_list,
            timeout_h=round(settings.extract_timeout / 3600, 1),
        ),
    )
