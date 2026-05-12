import logging
import traceback
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import OsType, IsoVersion, BootEntry
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ipxe-menus")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def _collect_menu_files() -> list[dict]:
    files = []
    if settings.menus_dir.exists():
        for f in sorted(settings.menus_dir.glob("*.ipxe")):
            files.append({
                "name": f.name,
                "content": f.read_text(encoding="utf-8"),
                "url": f"{settings.server_base_url}/menus/{f.name}",
                "size": f.stat().st_size,
            })
    return files


def _collect_custom_scripts(db: Session) -> list[dict]:
    """Retourne tous les BootEntry ayant un custom_ipxe_path."""
    scripts = []
    entries = (
        db.query(BootEntry)
        .filter(BootEntry.custom_ipxe_path.isnot(None))
        .join(BootEntry.iso_version)
        .join(IsoVersion.os_type)
        .all()
    )
    http_root = Path(settings.http_root)
    for e in entries:
        path = http_root / e.custom_ipxe_path
        content = ""
        size = 0
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                size = path.stat().st_size
            except Exception:
                pass
        scripts.append({
            "boot_entry_id": e.id,
            "os_label":      e.iso_version.os_type.label,
            "os_slug":       e.iso_version.os_type.slug,
            "version_label": e.iso_version.version_label,
            "filename":      Path(e.custom_ipxe_path).name,
            "rel_path":      e.custom_ipxe_path,
            "url":           f"{settings.server_base_url}/{e.custom_ipxe_path}",
            "size":          size,
            "content":       content,
        })
    return scripts


@router.get("", response_class=HTMLResponse)
async def menus_list(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    return templates.TemplateResponse(
        "menus.html",
        {
            "request":        request,
            "menu_files":     _collect_menu_files(),
            "custom_scripts": _collect_custom_scripts(db),
            "os_types":       db.query(OsType).all(),
            "server_url":     settings.server_base_url,
        },
    )


@router.post("/regenerate")
async def regenerate(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    try:
        from app.services.menu_generator import regenerate_all
        written = regenerate_all(db)
        logger.info("Menus régénérés : %s", written)
    except Exception:
        err = traceback.format_exc()
        logger.error("Erreur régénération menus :\n%s", err)
        os_types = db.query(OsType).all()
        menu_files = []
        if settings.menus_dir.exists():
            for f in sorted(settings.menus_dir.glob("*.ipxe")):
                menu_files.append({
                    "name": f.name,
                    "content": f.read_text(encoding="utf-8"),
                    "url": f"{settings.server_base_url}/menus/{f.name}",
                    "size": f.stat().st_size,
                })
        return templates.TemplateResponse(
            "menus.html",
            {
                "request": request,
                "menu_files": menu_files,
                "os_types": os_types,
                "server_url": settings.server_base_url,
                "error": err,
            },
            status_code=500,
        )

    # Also queue async in Celery if available
    try:
        from app.tasks.jobs import regenerate_menus_task
        regenerate_menus_task.delay()
    except Exception:
        pass

    return RedirectResponse("/ipxe-menus", status_code=302)


# ── Gestion des scripts personnalisés ─────────────────────────────────────────

@router.post("/custom/{entry_id}/save")
async def custom_script_save(
    entry_id: int,
    request: Request,
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    entry = db.query(BootEntry).get(entry_id)
    if not entry or not entry.custom_ipxe_path:
        raise HTTPException(404)

    path = Path(settings.http_root) / entry.custom_ipxe_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    # Regénérer le menu _autres concerné
    try:
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)
    except Exception:
        logger.exception("Erreur régénération menus après save script")

    return RedirectResponse("/ipxe-menus?tab=custom", status_code=302)


@router.post("/custom/{entry_id}/delete")
async def custom_script_delete(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    entry = db.query(BootEntry).get(entry_id)
    if not entry or not entry.custom_ipxe_path:
        raise HTTPException(404)

    # Supprimer le fichier disque
    path = Path(settings.http_root) / entry.custom_ipxe_path
    if path.exists():
        try:
            path.unlink()
        except Exception:
            logger.exception("Impossible de supprimer %s", path)

    # Effacer le champ en base
    entry.custom_ipxe_path = None
    db.commit()

    # Regénérer les menus
    try:
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)
    except Exception:
        logger.exception("Erreur régénération menus après delete script")

    return RedirectResponse("/ipxe-menus?tab=custom", status_code=302)


@router.get("/{filename}/raw", response_class=PlainTextResponse)
async def raw_menu(filename: str, request: Request):
    redir = _auth(request)
    if redir:
        return redir
    f = settings.menus_dir / filename
    if not f.exists() or not f.suffix == ".ipxe":
        raise HTTPException(404)
    return f.read_text(encoding="utf-8")


@router.post("/{filename}/save")
async def save_menu_override(
    filename: str,
    request: Request,
    content: str = Form(...),
):
    redir = _auth(request)
    if redir:
        return redir
    f = settings.menus_dir / filename
    if not f.suffix == ".ipxe":
        raise HTTPException(400)
    settings.menus_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return RedirectResponse("/ipxe-menus", status_code=302)
