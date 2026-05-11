from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import OsType
from app.config import settings

router = APIRouter(prefix="/ipxe-menus")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("", response_class=HTMLResponse)
async def menus_list(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    menu_files = []
    if settings.menus_dir.exists():
        for f in sorted(settings.menus_dir.glob("*.ipxe")):
            menu_files.append({
                "name": f.name,
                "content": f.read_text(encoding="utf-8"),
                "url": f"{settings.server_base_url}/menus/{f.name}",
                "size": f.stat().st_size,
            })

    os_types = db.query(OsType).all()
    return templates.TemplateResponse(
        "menus.html",
        {
            "request": request,
            "menu_files": menu_files,
            "os_types": os_types,
            "server_url": settings.server_base_url,
        },
    )


@router.post("/regenerate")
async def regenerate(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    # Regenerate synchronously for immediate result
    from app.services.menu_generator import regenerate_all
    regenerate_all(db)

    # Also queue async in Celery if available
    try:
        from app.tasks.jobs import regenerate_menus_task
        regenerate_menus_task.delay()
    except Exception:
        pass  # Celery not available — sync generation above is enough

    return RedirectResponse("/menus", status_code=302)


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
    return RedirectResponse("/menus", status_code=302)
