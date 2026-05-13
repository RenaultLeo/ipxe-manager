"""
Router /firmware — compilation et gestion des firmwares iPXE.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firmware")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


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

    # Lire l'embed actuel s'il existe
    embed_path = src / "src" / "embed.ipxe"
    embed_content = embed_path.read_text(encoding="utf-8") if embed_path.exists() else ""

    return {
        "undionly":     file_info(tftp / "undionly.kpxe"),
        "efi":          file_info(tftp / "ipxe.efi"),
        "snponly":      file_info(tftp / "snponly.efi"),
        "embed":        embed_content,
        "src_cloned":   (src / ".git").exists(),
        "tftp_dir":     str(tftp),
        "build_dir":    settings.build_dir,
    }


def _active_build() -> dict | None:
    """Retourne le job compile_ipxe en cours s'il existe."""
    try:
        from celery.app.control import Inspect
        from app.tasks.celery_app import celery
        insp = celery.control.inspect(timeout=1)
        active = insp.active() or {}
        for worker_tasks in active.values():
            for t in worker_tasks:
                if t.get("name") == "compile_ipxe":
                    return t
    except Exception:
        pass
    return None


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

    status   = _firmware_status()
    building = _active_build()
    result   = _build_result(task_id) if task_id else None
    menu_url = f"{settings.server_base_url}/menus/menu.ipxe"

    return templates.TemplateResponse(
        "firmware.html",
        {
            "request":   request,
            "status":    status,
            "building":  building,
            "result":    result,
            "task_id":   task_id,
            "menu_url":  menu_url,
            "tftp_root": settings.tftp_root,
        },
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
        menu_url = f"{settings.server_base_url}/menus/menu.ipxe"

    try:
        from app.tasks.jobs import compile_ipxe_task
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
        {
            "request":          request,
            "task_id":          task_id,
            "state":            state,
            "step":             step,
            "logs":             logs,
            "completed_steps":  completed_steps,
            "done":             done,
            "success":          success,
            "result":           result,
            "error":            error,
            "tftp_root":        settings.tftp_root,
        },
    )
