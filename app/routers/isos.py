import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import OsType, IsoVersion, Upload
from app.services.disk_info import fmt_size
from app.services.os_type_order import sort_os_types_for_ui
from app.templating import templates, template_context
from app.config import settings

router = APIRouter(prefix="/isos")
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
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    versions = db.query(IsoVersion).order_by(IsoVersion.created_at.desc()).all()
    return templates.TemplateResponse(
        "isos/index.html",
        template_context(
            request,
            os_types=os_types,
            versions=versions,
            fmt_size=fmt_size,
        ),
    )


# ── Upload form ───────────────────────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    return templates.TemplateResponse(
        "isos/upload.html",
        template_context(request, os_types=os_types),
    )


@router.post("/upload")
async def upload_iso(
    request: Request,
    os_type_id: int = Form(...),
    version_label: str = Form(...),
    notes: str = Form(""),
    file: UploadFile = File(None),
    # Windows boot files
    file_boot_wim: UploadFile = File(None),
    file_bcd:      UploadFile = File(None),
    file_boot_sdi: UploadFile = File(None),
    file_bootmgr:  UploadFile = File(None),
    # Linux boot files
    file_kernel:      UploadFile = File(None),
    file_initrd:      UploadFile = File(None),
    kernel_args:      str = Form(""),
    # Alpine modloop
    file_modloop:     UploadFile = File(None),
    # Script iPXE custom (tous OS)
    file_custom_ipxe: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    os_type = db.query(OsType).get(os_type_id)
    if not os_type:
        raise HTTPException(404, "Type d'OS introuvable")

    # ── Créer l'entrée en BDD d'abord ──────────────────────
    version = IsoVersion(
        os_type_id=os_type_id,
        version_label=version_label,
        status="uploaded",
        iso_size=0,
        notes=notes,
    )
    db.add(version)
    db.flush()  # obtenir version.id

    # ── ISO (optionnel) ────────────────────────────────────
    if file and file.filename:
        safe_name = Path(file.filename).name
        ext = Path(safe_name).suffix.lower()
        if ext not in {".iso", ".img", ""}:
            raise HTTPException(400, f"Extension non supportée : {ext}")

        iso_dir = Path(settings.iso_root) / os_type.slug
        iso_dir.mkdir(parents=True, exist_ok=True)
        dest = iso_dir / safe_name
        size = 0
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
                size += len(chunk)
                if size > settings.max_upload_size:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "Fichier trop volumineux")
        version.iso_path = str(dest)
        version.iso_size = size
        db.add(Upload(filename=safe_name, file_type="iso", size=size, status="done"))

    # ── Fichiers boot manuels ──────────────────────────────
    from app.services.slugify import slugify
    version_slug = slugify(version.version_label)

    boot_dir = settings.boot_dir / os_type.slug / version_slug
    boot_dir.mkdir(parents=True, exist_ok=True)

    from app.models.models import BootEntry
    be = BootEntry(iso_version_id=version.id, kernel_args=kernel_args)
    db.add(be)

    async def save_boot_file(upload: UploadFile, fname: str) -> str:
        dest = boot_dir / fname
        with open(dest, "wb") as f:
            while chunk := await upload.read(1024 * 1024):
                f.write(chunk)
        return f"boot/{os_type.slug}/{version_slug}/{fname}"

    has_boot_files = False

    if os_type.boot_type == "windows":
        if file_bcd and file_bcd.filename:
            be.bcd_path = await save_boot_file(file_bcd, "BCD")
            has_boot_files = True
        if file_boot_sdi and file_boot_sdi.filename:
            be.boot_sdi_path = await save_boot_file(file_boot_sdi, "boot.sdi")
            has_boot_files = True
        if file_boot_wim and file_boot_wim.filename:
            be.boot_wim_path = await save_boot_file(file_boot_wim, "boot.wim")
            has_boot_files = True
        if file_bootmgr and file_bootmgr.filename:
            be.bootmgr_path = await save_boot_file(file_bootmgr, file_bootmgr.filename)
            has_boot_files = True
    else:
        if file_kernel and file_kernel.filename:
            be.kernel_path = await save_boot_file(file_kernel, Path(file_kernel.filename).name)
            has_boot_files = True
        if file_initrd and file_initrd.filename:
            be.initrd_path = await save_boot_file(file_initrd, Path(file_initrd.filename).name)
            has_boot_files = True

    # ── Script iPXE custom (optionnel, tous OS) ───────────────
    if file_modloop and file_modloop.filename:
        be.modloop_path = await save_boot_file(file_modloop, Path(file_modloop.filename).name)
        has_boot_files = True

    if file_custom_ipxe and file_custom_ipxe.filename:
        be.custom_ipxe_path = await save_boot_file(file_custom_ipxe, Path(file_custom_ipxe.filename).name)
        has_boot_files = True

    if has_boot_files:
        version.status = "ready"

    db.commit()

    # Régénérer les menus si la version est prête
    if version.status == "ready":
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)

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
        template_context(request, version=version, fmt_size=fmt_size),
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
        file_type="extraction",
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
        template_context(
            request, status=version.status, version_id=version_id
        ),
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

    try:
        os_slug = version.os_type.slug

        # 1. Supprimer le fichier ISO du disque
        if version.iso_path:
            Path(version.iso_path).unlink(missing_ok=True)

        # 2. Supprimer les fichiers boot (dossier slug ET dossier ID pour compat)
        from app.services.slugify import slugify
        version_slug = slugify(version.version_label)
        for boot_path in [
            settings.boot_dir / os_slug / version_slug,
            settings.boot_dir / os_slug / str(version_id),
        ]:
            if boot_path.exists():
                shutil.rmtree(boot_path, ignore_errors=True)

        # 3. Supprimer les fichiers de config auto
        for cfg_path in [
            settings.configs_dir / os_slug / version_slug,
            settings.configs_dir / os_slug / str(version_id),
        ]:
            if cfg_path.exists():
                shutil.rmtree(cfg_path, ignore_errors=True)

        # 4. Supprimer l'entrée en DB (cascade : BootEntry + AutoConfigs)
        db.delete(version)
        db.commit()

        # 5. Régénérer les menus
        try:
            from app.services.menu_generator import regenerate_all
            regenerate_all(db)
        except Exception:
            pass

    except Exception as exc:
        import traceback
        traceback.print_exc()
        db.rollback()
        raise HTTPException(500, f"Erreur lors de la suppression : {exc}")

    return RedirectResponse("/isos", status_code=302)
