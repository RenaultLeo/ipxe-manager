import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import OsType, IsoVersion, Upload
from app.services.disk_info import fmt_size
from app.config import settings

router = APIRouter(prefix="/isos")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
TEMPLATES = templates


def _auth(request: Request):
    if not is_authenticated(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=302)
    return None


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def iso_list(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    os_types = db.query(OsType).all()
    versions = db.query(IsoVersion).order_by(IsoVersion.created_at.desc()).all()
    return templates.TemplateResponse(
        "isos/index.html",
        {
            "request": request,
            "os_types": os_types,
            "versions": versions,
            "fmt_size": fmt_size,
        },
    )


# ── Upload form ───────────────────────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    os_types = db.query(OsType).all()
    return templates.TemplateResponse(
        "isos/upload.html",
        {"request": request, "os_types": os_types},
    )


@router.post("/upload")
async def upload_iso(
    request: Request,
    os_type_id: int = Form(...),
    version_label: str = Form(...),
    notes: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    os_type = db.query(OsType).get(os_type_id)
    if not os_type:
        raise HTTPException(404, "Type d'OS introuvable")

    iso_dir = Path(settings.iso_root) / os_type.slug
    iso_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename).name
    ext = Path(safe_name).suffix.lower()
    if ext not in {".iso", ".img", ""}:
        raise HTTPException(400, f"Extension non supportée : {ext}")

    dest = iso_dir / safe_name

    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            f.write(chunk)
            size += len(chunk)
            if size > settings.max_upload_size:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Fichier trop volumineux")

    version = IsoVersion(
        os_type_id=os_type_id,
        version_label=version_label,
        status="uploaded",
        iso_path=str(dest),
        iso_size=size,
        notes=notes,
    )
    db.add(version)
    db.flush()

    upload_log = Upload(
        filename=safe_name,
        file_type="iso",
        size=size,
        status="pending",
    )
    db.add(upload_log)
    db.flush()
    db.commit()

    return RedirectResponse(f"/isos/{version.id}", status_code=302)


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{version_id}", response_class=HTMLResponse)
async def iso_detail(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404, "Version introuvable")
    return templates.TemplateResponse(
        "isos/detail.html",
        {"request": request, "version": version, "fmt_size": fmt_size},
    )


# ── Extract ───────────────────────────────────────────────────────────────────

@router.post("/{version_id}/extract")
async def extract(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)

    upload_log = Upload(
        filename=Path(version.iso_path).name,
        file_type="iso",
        size=version.iso_size,
        status="pending",
    )
    db.add(upload_log)
    db.commit()

    from app.tasks.jobs import extract_iso_task
    extract_iso_task.delay(version_id, upload_log.id)

    return RedirectResponse(f"/isos/{version_id}", status_code=302)


# ── Job status (HTMX polling) ─────────────────────────────────────────────────

@router.get("/{version_id}/status")
async def iso_status(version_id: int, db: Session = Depends(get_db)):
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)
    return JSONResponse({"status": version.status})


@router.get("/{version_id}/status-fragment", response_class=HTMLResponse)
async def iso_status_fragment(version_id: int, request: Request, db: Session = Depends(get_db)):
    """HTMX endpoint — retourne uniquement le badge de statut HTML."""
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "isos/status_badge.html",
        {"request": request, "status": version.status, "version_id": version_id},
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{version_id}/delete")
async def delete_iso(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)

    # Remove ISO file
    if version.iso_path and Path(version.iso_path).exists():
        Path(version.iso_path).unlink(missing_ok=True)

    # Remove extracted boot files
    from app.services.iso_extractor import cleanup_boot_files
    cleanup_boot_files(version.os_type.slug, version.id)

    db.delete(version)
    db.commit()

    # Regenerate menus
    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()

    return RedirectResponse("/isos", status_code=302)
