from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import IsoVersion, AutoConfig
from app.config import settings

router = APIRouter(prefix="/ipxe-configs")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

CONFIG_TYPES = ["preseed", "kickstart", "unattend", "cloud-init", "custom"]


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("", response_class=HTMLResponse)
async def config_list(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    configs = db.query(AutoConfig).order_by(AutoConfig.updated_at.desc()).all()
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/index.html",
        {"request": request, "configs": configs, "versions": versions,
         "config_types": CONFIG_TYPES},
    )


@router.get("/new", response_class=HTMLResponse)
async def config_new(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/edit.html",
        {"request": request, "config": None, "versions": versions,
         "config_types": CONFIG_TYPES, "server_url": settings.server_base_url},
    )


@router.post("/new")
async def config_create(
    request: Request,
    iso_version_id: int = Form(...),
    config_type: str = Form(...),
    label: str = Form(""),
    content: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    version = db.query(IsoVersion).get(iso_version_id)
    if not version:
        raise HTTPException(404)

    cfg = AutoConfig(
        iso_version_id=iso_version_id,
        config_type=config_type,
        label=label or config_type,
        content=content,
    )
    db.add(cfg)
    db.flush()

    file_path = _write_config_file(cfg, version, content)
    cfg.file_path = file_path
    db.commit()

    return RedirectResponse("/ipxe-configs", status_code=302)


@router.get("/{config_id}/edit", response_class=HTMLResponse)
async def config_edit(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/edit.html",
        {"request": request, "config": cfg, "versions": versions,
         "config_types": CONFIG_TYPES, "server_url": settings.server_base_url},
    )


@router.post("/{config_id}/edit")
async def config_update(
    config_id: int,
    request: Request,
    label: str = Form(""),
    content: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)

    cfg.label = label or cfg.config_type
    cfg.content = content
    cfg.updated_at = datetime.utcnow()
    file_path = _write_config_file(cfg, cfg.iso_version, content)
    cfg.file_path = file_path
    db.commit()

    return RedirectResponse("/ipxe-configs", status_code=302)


@router.post("/{config_id}/delete")
async def config_delete(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)
    if cfg.file_path:
        Path(settings.http_root).joinpath(cfg.file_path).unlink(missing_ok=True)
    db.delete(cfg)
    db.commit()
    return RedirectResponse("/ipxe-configs", status_code=302)


def _write_config_file(cfg: AutoConfig, version: IsoVersion, content: str) -> str:
    """Write config content to disk and return the relative path."""
    cfg_dir = settings.configs_dir / version.os_type.slug / str(version.id)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    ext_map = {
        "preseed": "cfg", "kickstart": "cfg",
        "unattend": "xml", "cloud-init": "yaml", "custom": "txt",
    }
    ext = ext_map.get(cfg.config_type, "txt")
    fname = f"{cfg.config_type}_{cfg.id}.{ext}"
    file = cfg_dir / fname
    file.write_text(content, encoding="utf-8")
    return f"configs/{version.os_type.slug}/{version.id}/{fname}"
