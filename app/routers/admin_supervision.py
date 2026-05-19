"""Supervision serveur : état, graphiques, contrôles d'intégrité, relance services."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import auth_redirect_admin
from app.database import get_db
from app.i18n import translate
from app.services.server_diagnostics import collect_snapshot, http_probe, systemctl_restart
from app.services.server_verification import (
    get_last_verification,
    run_full_exhaustive,
    run_quick_verification,
)
from app.templating import templates, template_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

SERVICE_UNITS_RESTART = ("ipxe-manager", "ipxe-celery", "tftpd-hpa")


@router.get("/supervision", response_class=HTMLResponse)
async def supervision_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    snap = collect_snapshot(db)
    last_ver = get_last_verification()
    return templates.TemplateResponse(
        "admin/supervision.html",
        template_context(
            request,
            snapshot=snap,
            last_verification=last_ver,
            msg=msg,
        ),
    )


@router.get("/supervision/api/snapshot")
async def supervision_snapshot_api(request: Request, db: Session = Depends(get_db)):
    redir = auth_redirect_admin(request)
    if redir:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    snap = collect_snapshot(db)
    base = snap.get("application", {}).get("server_base_url", "")
    snap["http_probe"] = http_probe(base or str(request.base_url).rstrip("/"), "/login")
    return JSONResponse(snap)


@router.post("/supervision/verification/quick")
async def supervision_quick_verify(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    result = run_quick_verification(request)
    lang = getattr(request.state, "locale", "fr")
    if result.get("ok"):
        msg = translate(lang, "super.verify_quick_ok", n=len(result.get("items", [])))
    else:
        msg = translate(
            lang,
            "super.verify_quick_fail",
            n=result.get("failures", 0),
        )
    return RedirectResponse(f"/admin/supervision?msg={quote(msg)}", status_code=302)


@router.post("/supervision/verification/full")
async def supervision_full_verify(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    result = run_full_exhaustive(request)
    lang = getattr(request.state, "locale", "fr")
    if result.get("ok"):
        msg = translate(lang, "super.verify_full_ok", sec=result.get("duration_sec", 0))
    else:
        msg = translate(lang, "super.verify_full_fail", sec=result.get("duration_sec", 0))
    return RedirectResponse(f"/admin/supervision?msg={quote(msg)}#integrity", status_code=302)


@router.post("/supervision/services/restart")
async def supervision_restart_services(request: Request):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    lines: list[str] = []
    ok_all = True
    for unit in SERVICE_UNITS_RESTART:
        info = systemctl_restart(unit)
        if info.get("ok"):
            sudo_note = " (sudo)" if info.get("sudo") else ""
            lines.append(translate(lang, "admin.service_ok", unit=unit) + sudo_note)
        else:
            ok_all = False
            lines.append(
                translate(
                    lang,
                    "admin.service_fail",
                    unit=unit,
                    detail=info.get("detail", ""),
                )
            )
    msg = " — ".join(lines)
    if ok_all:
        msg = translate(lang, "admin.services_restarted") + " " + msg
    else:
        msg = translate(lang, "super.restart_partial") + " " + msg
    return RedirectResponse(f"/admin/supervision?msg={quote(msg)}", status_code=302)
