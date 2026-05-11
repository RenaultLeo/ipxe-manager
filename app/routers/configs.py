from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import OsType, IsoVersion, AutoConfig
from app.config import settings
from app.services.config_scanner import OS_CONFIG_TYPE

router = APIRouter(prefix="/ipxe-configs")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

CONFIG_TYPES = ["preseed", "kickstart", "unattend", "cloud-init", "custom"]


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("", response_class=HTMLResponse)
async def config_list(request: Request, db: Session = Depends(get_db),
                      scan_result: str = ""):
    redir = _auth(request)
    if redir:
        return redir
    configs = db.query(AutoConfig).order_by(AutoConfig.updated_at.desc()).all()
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/index.html",
        {"request": request, "configs": configs, "versions": versions,
         "config_types": CONFIG_TYPES, "scan_result": scan_result},
    )


@router.post("/scan")
async def config_scan(request: Request, db: Session = Depends(get_db)):
    """Scan configs/ directory and auto-import unregistered config files."""
    redir = _auth(request)
    if redir:
        return redir
    from app.services.config_scanner import scan_and_import
    res = scan_and_import(db)
    msg = f"Scan terminé — {res['imported']} importé(s), {res['skipped']} ignoré(s)"
    if res["errors"]:
        msg += f", {len(res['errors'])} erreur(s)"
    return RedirectResponse(f"/ipxe-configs?scan_result={msg}", status_code=302)


@router.get("/new", response_class=HTMLResponse)
async def config_new(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/edit.html",
        {"request": request, "config": None, "versions": versions,
         "config_types": CONFIG_TYPES, "server_url": settings.server_base_url,
         "os_config_type": OS_CONFIG_TYPE},
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
         "config_types": CONFIG_TYPES, "server_url": settings.server_base_url,
         "os_config_type": OS_CONFIG_TYPE},
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
    from app.services.slugify import slugify
    version_slug = slugify(version.version_label)
    cfg_dir = settings.configs_dir / version.os_type.slug / version_slug
    cfg_dir.mkdir(parents=True, exist_ok=True)

    ext_map = {
        "preseed":    "cfg",
        "kickstart":  "cfg",
        "unattend":   "xml",
        "cloud-init": "yaml",
        "custom":     "txt",
    }
    ext = ext_map.get(cfg.config_type, "txt")

    # Nom de fichier = label slugifié, ou type si label vide
    base = slugify(cfg.label) if cfg.label and slugify(cfg.label) else cfg.config_type
    fname = f"{base}.{ext}"
    dest = cfg_dir / fname

    # Si le fichier existe déjà et appartient à une autre config, ajouter l'ID
    if dest.exists():
        existing_rel = f"configs/{version.os_type.slug}/{version_slug}/{fname}"
        from app.database import SessionLocal
        db_check = SessionLocal()
        conflict = db_check.query(AutoConfig).filter(
            AutoConfig.file_path == existing_rel,
            AutoConfig.id != cfg.id,
        ).first()
        db_check.close()
        if conflict:
            fname = f"{base}_{cfg.id}.{ext}"
            dest = cfg_dir / fname

    dest.write_text(content, encoding="utf-8")
    return f"configs/{version.os_type.slug}/{version_slug}/{fname}"
