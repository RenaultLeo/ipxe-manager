"""Administration : comptes utilisateurs et relance des services."""
from __future__ import annotations

import logging
import re
import subprocess

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import ROLE_ADMIN, ROLE_USER, auth_redirect_admin, hash_password
from app.database import get_db
from app.i18n import translate
from app.models.models import User
from app.templating import templates, template_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{2,31}$")
SERVICE_UNITS = ("ipxe-manager", "ipxe-celery")


def _normalize_username(raw: str) -> str:
    return (raw or "").strip().lower()


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    users = db.query(User).order_by(User.role.desc(), User.username.asc()).all()
    return templates.TemplateResponse(
        "admin/users.html",
        template_context(request, users=users, msg=msg),
    )


@router.post("/users/create")
async def users_create(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(ROLE_USER),
):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    name = _normalize_username(username)
    if not USERNAME_RE.match(name):
        err = translate(lang, "admin.user_invalid_username")
        return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    if len(password) < 6:
        err = translate(lang, "admin.user_password_short")
        return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    if db.query(User).filter(User.username == name).first():
        err = translate(lang, "admin.user_exists")
        return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    r = ROLE_ADMIN if (role or "").strip() == ROLE_ADMIN else ROLE_USER
    if name == "admin" and r != ROLE_ADMIN:
        r = ROLE_ADMIN
    db.add(User(username=name, password_hash=hash_password(password), role=r))
    db.commit()
    ok = translate(lang, "admin.user_created")
    return RedirectResponse(f"/admin/users?msg={ok}", status_code=302)


@router.post("/users/{user_id}/password")
async def users_set_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    new_password: str = Form(...),
):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    row = db.query(User).get(user_id)
    if not row:
        raise HTTPException(404)
    if len(new_password) < 6:
        err = translate(lang, "admin.user_password_short")
        return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    row.password_hash = hash_password(new_password)
    db.commit()
    ok = translate(lang, "admin.user_password_updated")
    return RedirectResponse(f"/admin/users?msg={ok}", status_code=302)


@router.post("/users/{user_id}/delete")
async def users_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    row = db.query(User).get(user_id)
    if not row:
        raise HTTPException(404)
    if row.role == ROLE_ADMIN:
        admins = db.query(User).filter(User.role == ROLE_ADMIN).count()
        if admins <= 1:
            err = translate(lang, "admin.user_last_admin")
            return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    from app.models.models import IsoVersion

    owned = db.query(IsoVersion).filter(IsoVersion.owner_user_id == row.id).count()
    if owned > 0:
        err = translate(lang, "admin.user_has_isos", n=owned)
        return RedirectResponse(f"/admin/users?msg={err}", status_code=302)
    db.delete(row)
    db.commit()
    ok = translate(lang, "admin.user_deleted")
    return RedirectResponse(f"/admin/users?msg={ok}", status_code=302)


@router.post("/services/restart")
async def services_restart(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    lines: list[str] = []
    ok_all = True
    for unit in SERVICE_UNITS:
        try:
            proc = subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                lines.append(translate(lang, "admin.service_ok", unit=unit))
            else:
                ok_all = False
                detail = (proc.stderr or proc.stdout or "").strip()[:200]
                lines.append(translate(lang, "admin.service_fail", unit=unit, detail=detail))
        except FileNotFoundError:
            ok_all = False
            lines.append(translate(lang, "admin.service_no_systemctl", unit=unit))
        except Exception as exc:
            ok_all = False
            logger.exception("Restart %s", unit)
            lines.append(translate(lang, "admin.service_fail", unit=unit, detail=str(exc)[:200]))
    msg = " — ".join(lines)
    if ok_all:
        msg = translate(lang, "admin.services_restarted") + " " + msg
    return RedirectResponse(f"/admin/users?msg={msg}", status_code=302)
