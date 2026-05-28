"""Supervision serveur : état, graphiques, contrôles d'intégrité, relance services."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import auth_redirect_admin
from app.config import sync_settings_server_base_url_from_db
from app.database import get_db, init_db
from app.i18n import translate
from app.services.server_diagnostics import (
    collect_snapshot_cached,
    invalidate_snapshot_cache,
    schedule_service_restarts,
)
from app.services.server_verification import (
    get_last_verification,
    persist_last_verification,
    run_full_exhaustive,
    run_quick_verification,
)
from app.templating import templates, template_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

_SESSION_FLASH_MSG = "supervision_flash_msg"


def _pop_supervision_flash(request: Request, query_msg: str = "") -> str:
    """Message one-shot après redirect (évite ?msg=… dans l’URL)."""
    flash = (request.session.pop(_SESSION_FLASH_MSG, None) or "").strip()
    q = (query_msg or "").strip()
    return flash or q


def _redirect_supervision_integrity(request: Request, msg: str) -> RedirectResponse:
    request.session[_SESSION_FLASH_MSG] = msg
    return RedirectResponse("/admin/supervision#integrity", status_code=302)

def _safe_return_url(raw: str | None, default: str = "/admin/supervision") -> str:
    """Chemin interne uniquement (évite open redirect)."""
    if not raw or not str(raw).strip():
        return default
    s = str(raw).strip()
    if s.startswith("/"):
        path = s.split("#", 1)[0]
        if path.startswith("/admin/supervision/services/"):
            return default
        return s if len(s) <= 512 else s[:512]
    try:
        p = urlparse(s)
    except ValueError:
        return default
    if p.scheme or p.netloc:
        path = p.path or default
    else:
        path = s
    if not path.startswith("/"):
        return default
    if path.startswith("/admin/supervision/services/"):
        return default
    out = path
    if p.query:
        out += "?" + p.query
    if p.fragment:
        out += "#" + p.fragment
    return out[:512]


@router.get("/supervision", response_class=HTMLResponse)
async def supervision_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    last_ver = get_last_verification()
    display_msg = _pop_supervision_flash(request, msg)
    return templates.TemplateResponse(
        "admin/supervision.html",
        template_context(
            request,
            snapshot={"loading": True},
            last_verification=last_ver,
            msg=display_msg,
        ),
    )


@router.get("/supervision/api/snapshot")
async def supervision_snapshot_api(
    request: Request,
    db: Session = Depends(get_db),
    full: bool = False,
    force: bool = False,
):
    redir = auth_redirect_admin(request)
    if redir:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    snap = await asyncio.to_thread(
        collect_snapshot_cached,
        db,
        force=full or force,
        quick=not full,
    )
    return JSONResponse(snap)


@router.post("/supervision/verification/quick")
async def supervision_quick_verify(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    invalidate_snapshot_cache()
    lang = getattr(request.state, "locale", "fr")
    try:
        result = await asyncio.to_thread(run_quick_verification, request)
    except Exception as exc:
        logger.exception("supervision_quick_verify")
        result = {
            "mode": "quick",
            "ok": False,
            "failures": 1,
            "checks": [],
            "log": str(exc)[:2000],
            "duration_sec": 0,
        }
        persist_last_verification(result)
    if result.get("ok"):
        msg = translate(lang, "super.verify_quick_ok", n=len(result.get("checks", [])))
    else:
        msg = translate(
            lang,
            "super.verify_quick_fail",
            n=result.get("failures", 0),
        )
    return _redirect_supervision_integrity(request, msg)


@router.post("/supervision/verification/full")
async def supervision_full_verify(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    invalidate_snapshot_cache()
    lang = getattr(request.state, "locale", "fr")
    try:
        result = await asyncio.to_thread(run_full_exhaustive, request)
    except Exception as exc:
        logger.exception("supervision_full_verify")
        result = {
            "mode": "full",
            "ok": False,
            "failures": 1,
            "checks": [],
            "log": str(exc)[:2000],
            "duration_sec": 0,
        }
        persist_last_verification(result)
    if result.get("ok"):
        msg = translate(lang, "super.verify_full_ok", sec=result.get("duration_sec", 0))
    else:
        msg = translate(lang, "super.verify_full_fail", sec=result.get("duration_sec", 0))
    return _redirect_supervision_integrity(request, msg)


@router.post("/supervision/database/sync")
async def supervision_sync_database(request: Request, db: Session = Depends(get_db)):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    try:
        invalidate_snapshot_cache()
        init_db()
        sync_settings_server_base_url_from_db()
        from app.models.models import OsType, User

        users = db.query(User).count()
        os_types = db.query(OsType).count()
        msg = translate(lang, "super.sync_db_ok", users=users, os_types=os_types)
    except Exception as exc:
        logger.exception("sync database")
        msg = translate(lang, "super.sync_db_fail", detail=str(exc)[:200])
    return _redirect_supervision_integrity(request, msg)


@router.get("/supervision/services/restarting", response_class=HTMLResponse)
async def supervision_restarting_page(request: Request, next: str = "/admin/supervision"):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    return_url = _safe_return_url(next)
    success_msg = translate(lang, "admin.services_restarted")
    return templates.TemplateResponse(
        "admin/restarting.html",
        template_context(
            request,
            return_url=return_url,
            success_msg=success_msg,
        ),
    )


@router.post("/supervision/services/restart")
async def supervision_restart_services(request: Request, next: str = Form("")):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    return_url = _safe_return_url(
        next or request.headers.get("referer"),
        "/admin/supervision",
    )
    schedule_service_restarts()
    return RedirectResponse(
        f"/admin/supervision/services/restarting?next={quote(return_url, safe='')}",
        status_code=303,
    )
