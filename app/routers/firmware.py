"""
Router /firmware — compilation et gestion des firmwares iPXE.
"""
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import auth_redirect_admin
from app.templating import templates, template_context
from app.config import settings, resolve_server_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firmware")

_EMBED_PREVIEW_MAX = 4000
_FW_STATUS_CACHE: tuple[float, dict] | None = None
_FW_STATUS_TTL = 15.0


def _auth(request: Request):
    return auth_redirect_admin(request)


def _read_embed_preview(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(_EMBED_PREVIEW_MAX)
    except OSError:
        logger.exception("lecture embed.ipxe")
        return ""


def _firmware_status() -> dict:
    """Retourne l'état actuel des binaires TFTP et de l'embed."""
    tftp = Path(settings.tftp_root)
    src  = settings.ipxe_src_dir

    def file_info(p: Path) -> dict | None:
        if not p.exists():
            return None
        st = p.stat()
        return {
            "path":    str(p),
            "size":    st.st_size,
            "mtime":   st.st_mtime,
        }

    embed_path = src / "src" / "embed.ipxe"
    embed_content = _read_embed_preview(embed_path)

    ca = settings.tls_ca_cert_path
    tls_ready = ca.is_file()
    base = resolve_server_base_url()
    tls_https_url = base.startswith("https://")

    return {
        "undionly":     file_info(tftp / "undionly.kpxe"),
        "efi":          file_info(tftp / "ipxe.efi"),
        "snponly":      file_info(tftp / "snponly.efi"),
        "embed":        embed_content,
        "src_cloned":   (src / ".git").exists(),
        "tftp_dir":     str(tftp),
        "build_dir":    settings.build_dir,
        "tls_ca_path":  str(ca),
        "tls_ready":    tls_ready,
        "tls_https_url": tls_https_url,
    }


def _firmware_status_cached() -> dict:
    global _FW_STATUS_CACHE
    now = time.monotonic()
    if _FW_STATUS_CACHE and now - _FW_STATUS_CACHE[0] < _FW_STATUS_TTL:
        return _FW_STATUS_CACHE[1]
    status = _firmware_status()
    _FW_STATUS_CACHE = (now, status)
    return status


def _active_build(timeout: float = 0.6) -> dict | None:
    """Retourne le job compile_ipxe en cours s'il existe."""
    try:
        from app.tasks.celery_app import celery
        insp = celery.control.inspect(timeout=timeout)
        active = insp.active() or {}
        for worker_tasks in active.values():
            for t in worker_tasks:
                if t.get("name") == "compile_ipxe":
                    return t
    except Exception:
        pass
    return None


@router.get("/api/status")
async def firmware_api_status(request: Request):
    """État léger (Celery) sans bloquer le rendu de la page."""
    redir = _auth(request)
    if redir:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(
        {
            "building": _active_build(timeout=0.6) is not None,
            "status": _firmware_status_cached(),
        }
    )


def _build_result(task_id: str) -> dict | None:
    """Retourne le résultat d'un job terminé."""
    try:
        from celery.result import AsyncResult
        r = AsyncResult(task_id)
        if r.ready():
            return {"state": r.state, "result": r.result if r.successful() else str(r.result)}
    except Exception:
        pass
    return None


@router.get("", response_class=HTMLResponse)
async def firmware_index(request: Request, task_id: str = "", db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    status   = _firmware_status_cached()
    building = _active_build(timeout=0.6) if task_id else None
    result   = _build_result(task_id) if task_id else None
    base = resolve_server_base_url(db)
    menu_url = f"{base}/menus/menu.ipxe"

    return templates.TemplateResponse(
        "firmware.html",
        template_context(
            request,
            status=status,
            building=building,
            result=result,
            task_id=task_id,
            menu_url=menu_url,
            tftp_root=settings.tftp_root,
        ),
    )


@router.post("/build")
async def firmware_build(
    request: Request,
    menu_url: str = Form(None),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    if not menu_url:
        menu_url = f"{resolve_server_base_url(db)}/menus/menu.ipxe"

    try:
        from app.tasks.jobs import compile_ipxe_task
        global _FW_STATUS_CACHE
        _FW_STATUS_CACHE = None
        task = compile_ipxe_task.delay(menu_url)
        return RedirectResponse(f"/firmware?task_id={task.id}", status_code=302)
    except Exception as exc:
        logger.exception("Impossible de lancer compile_ipxe_task")
        return RedirectResponse("/firmware?error=celery", status_code=302)


@router.post("/cancel/{task_id}")
async def firmware_cancel(task_id: str, request: Request):
    redir = _auth(request)
    if redir:
        return redir

    try:
        from celery.result import AsyncResult
        from app.tasks.celery_app import celery
        celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
        AsyncResult(task_id).forget()
        logger.info("compile_ipxe_task %s annulé", task_id)
    except Exception:
        logger.exception("Erreur annulation task %s", task_id)

    return RedirectResponse("/firmware?cancelled=1", status_code=302)


@router.get("/status/{task_id}", response_class=HTMLResponse)
async def firmware_task_status(task_id: str, request: Request):
    """Endpoint HTMX pour le polling du statut de compilation."""
    redir = _auth(request)
    if redir:
        return redir

    try:
        from celery.result import AsyncResult
        r = AsyncResult(task_id)
        state  = r.state
        meta   = r.info or {}
        step   = meta.get("step", "") if isinstance(meta, dict) else ""
        logs   = meta.get("logs", []) if isinstance(meta, dict) else []
        completed_steps = meta.get("completed_steps", []) if isinstance(meta, dict) else []
        done   = r.ready()
        success = r.successful() if done else False
        result  = r.result if (done and success) else None
        error   = str(r.result) if (done and not success) else None
    except Exception as exc:
        state, step, logs, completed_steps, done, success, result, error = (
            "UNKNOWN", "", [], [], False, False, None, str(exc),
        )

    return templates.TemplateResponse(
        "firmware_status.html",
        template_context(
            request,
            task_id=task_id,
            state=state,
            step=step,
            logs=logs,
            completed_steps=completed_steps,
            done=done,
            success=success,
            result=result,
            error=error,
            tftp_root=settings.tftp_root,
        ),
    )
