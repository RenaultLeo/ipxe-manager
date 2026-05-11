from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated, hash_password
from app.models.models import AppSetting, OsType
from app.config import settings as app_settings

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

EDITABLE_KEYS = ["server_base_url", "admin_password_hash"]


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    current = {
        "server_base_url": _get_setting(db, "server_base_url", app_settings.server_base_url),
        "tftp_root": app_settings.tftp_root,
        "http_root": app_settings.http_root,
        "iso_root": app_settings.iso_root,
    }
    os_types = db.query(OsType).all()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "current": current, "os_types": os_types},
    )


@router.post("/server-url")
async def update_server_url(
    request: Request,
    server_base_url: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    _set_setting(db, "server_base_url", server_base_url.rstrip("/"))
    # Update runtime setting so next menu gen uses new URL
    app_settings.server_base_url = server_base_url.rstrip("/")
    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()
    return RedirectResponse("/settings", status_code=302)


@router.post("/password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    _set_setting(db, "admin_password_hash", hash_password(new_password))
    return RedirectResponse("/settings?msg=password_updated", status_code=302)


@router.post("/os-types/add")
async def add_os_type(
    request: Request,
    slug: str = Form(...),
    label: str = Form(...),
    icon: str = Form("bi-hdd"),
    boot_type: str = Form("linux"),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    if not db.query(OsType).filter(OsType.slug == slug).first():
        db.add(OsType(slug=slug, label=label, icon=icon, boot_type=boot_type))
        db.commit()
    return RedirectResponse("/settings", status_code=302)


@router.post("/os-types/{os_id}/delete")
async def delete_os_type(os_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    ot = db.query(OsType).get(os_id)
    if ot:
        db.delete(ot)
        db.commit()
    return RedirectResponse("/settings", status_code=302)
