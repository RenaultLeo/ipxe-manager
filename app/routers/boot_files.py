import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import IsoVersion, BootEntry, Upload
from app.services.disk_info import fmt_size
from app.config import settings

router = APIRouter(prefix="/boot-files")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("", response_class=HTMLResponse)
async def boot_list(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    versions = (
        db.query(IsoVersion)
        .filter(IsoVersion.status.in_(["uploaded", "ready"]))
        .all()
    )
    return templates.TemplateResponse(
        "boot_files.html",
        {"request": request, "versions": versions, "fmt_size": fmt_size,
         "server_url": settings.server_base_url},
    )


@router.post("/{version_id}/upload")
async def upload_boot_file(
    version_id: int,
    request: Request,
    file_role: str = Form(...),  # kernel|initrd|boot_wim|efi|other
    kernel_args: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)

    from app.services.slugify import slugify
    version_slug = slugify(version.version_label)
    dest_dir = settings.boot_dir / version.os_type.slug / version_slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename).name
    # Basic security: strip directory traversal
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        raise HTTPException(400, "Nom de fichier invalide")

    dest = dest_dir / safe_name
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
            size += len(chunk)

    relative = f"boot/{version.os_type.slug}/{version_slug}/{safe_name}"

    be = version.boot_entry
    if not be:
        be = BootEntry(iso_version_id=version_id)
        db.add(be)
        db.flush()

    if file_role == "kernel":
        be.kernel_path   = relative
    elif file_role == "initrd":
        be.initrd_path   = relative
    elif file_role == "boot_wim":
        be.boot_wim_path = relative
    elif file_role == "bcd":
        be.bcd_path      = relative
    elif file_role == "boot_sdi":
        be.boot_sdi_path = relative
    elif file_role == "bootmgr":
        be.bootmgr_path  = relative
    elif file_role == "efi":
        be.efi_path      = relative

    if kernel_args:
        be.kernel_args = kernel_args

    be.updated_at = datetime.utcnow()

    if version.status != "ready":
        version.status = "ready"

    db.add(Upload(filename=safe_name, file_type=file_role, size=size, status="done"))
    db.commit()

    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()

    return RedirectResponse("/boot-files", status_code=302)


@router.post("/{version_id}/args")
async def update_kernel_args(
    version_id: int,
    request: Request,
    kernel_args: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)

    be = version.boot_entry
    if not be:
        be = BootEntry(iso_version_id=version_id)
        db.add(be)
        db.flush()

    be.kernel_args = kernel_args
    be.updated_at = datetime.utcnow()
    db.commit()

    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()

    return RedirectResponse("/boot-files", status_code=302)
