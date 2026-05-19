from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import auth_redirect_login, get_session_user, is_admin
from app.services.ownership import filter_iso_versions, filter_uploads
from app.services.disk_info import get_disk_usage, fmt_size
from app.models.models import OsType, IsoVersion, Upload
from app.services.os_type_order import visible_on_dashboard
from app.config import settings
from app.templating import templates, template_context

router = APIRouter()


def _auth_redirect(request: Request):
    return auth_redirect_login(request)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    redir = _auth_redirect(request)
    if redir:
        return redir

    try:
        disk = get_disk_usage()
        all_os_types = db.query(OsType).all()
        os_types = visible_on_dashboard(all_os_types)
        os_type_count = len(all_os_types)

        user = get_session_user(request)
        stats = []
        for ot in os_types:
            base = filter_iso_versions(db, user).filter(IsoVersion.os_type_id == ot.id)
            total = base.count()
            ready = base.filter(IsoVersion.status == "ready").count()
            stats.append({"os": ot, "total": total, "ready": ready})

        recent_uploads = (
            filter_uploads(db, user)
            .order_by(Upload.created_at.desc())
            .limit(10)
            .all()
        )
        active_jobs_list = (
            filter_uploads(db, user)
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
        os_type_count = 0

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
            os_type_count=os_type_count,
            show_kill_all=is_admin(request),
        ),
    )
