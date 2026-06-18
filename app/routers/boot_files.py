import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from starlette.datastructures import UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from urllib.parse import quote

from app.database import get_db
from app.auth import auth_redirect_admin, auth_redirect_login, get_session_user
from app.services.ownership import filter_iso_versions, get_iso_version
from app.models.models import OsType, IsoVersion, BootEntry, Upload
from app.services.os_type_order import sort_os_types_for_ui
from app.services.disk_info import fmt_size
from app.templating import templates, template_context
from app.config import settings, resolve_server_base_url
from app.i18n import translate

router = APIRouter(prefix="/boot-files")


def _safe_redirect_path(raw: str, default: str) -> str:
    p = (raw or "").strip()
    if not p.startswith("/") or p.startswith("//") or "://" in p:
        return default
    return p


def _auth(request: Request):
    return auth_redirect_login(request)


@router.get("", response_class=HTMLResponse)
async def boot_list(
    request: Request,
    db: Session = Depends(get_db),
    scan_result: str = "",
    os: str | None = Query(None, description="Slug du type d'OS : onglet pré-sélectionné."),
):
    redir = _auth(request)
    if redir:
        return redir
    os_types = [
        ot
        for ot in sort_os_types_for_ui(db.query(OsType).all())
        if (ot.slug or "").lower() != "winpe"
    ]
    versions = (
        db.query(IsoVersion)
        .options(
            joinedload(IsoVersion.os_type),
            joinedload(IsoVersion.boot_entry),
        )
        .filter(IsoVersion.status.in_(["uploaded", "ready", "extracting", "error"]))
        .order_by(IsoVersion.created_at.desc())
        .all()
    )
    slug_set = {ot.slug for ot in os_types}
    raw = (os or "").strip().lower()
    filter_os_slug = raw if raw in slug_set else ""
    return templates.TemplateResponse(
        "boot_files.html",
        template_context(
            request,
            os_types=os_types,
            versions=versions,
            filter_os_slug=filter_os_slug,
            fmt_size=fmt_size,
            server_url=resolve_server_base_url(db),
            scan_result=scan_result,
        ),
    )


@router.post("/scan")
async def scan_boot_files(
    request: Request,
    db: Session = Depends(get_db),
    return_os: str = Form(""),
):
    """Scanne boot/ et enregistre les fichiers existants en DB."""
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    from app.services.boot_scanner import scan_and_register
    res = scan_and_register(db)

    # Régénérer les menus avec les nouveaux chemins
    try:
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
    except Exception:
        pass

    lang = getattr(request.state, "locale", "fr")
    msg = translate(
        lang,
        "boot.scan_done",
        updated=res["updated"],
        skipped=res["skipped"],
    )
    if res.get("errors"):
        msg += translate(lang, "boot.scan_errors_suffix", n=len(res["errors"]))
    dest = f"/boot-files?scan_result={quote(msg)}"
    ro = (return_os or "").strip().lower()
    if ro:
        dest += f"&os={quote(ro)}"
    return RedirectResponse(dest, status_code=302)


def _pick_upload_file(form, key: str) -> UploadFile | None:
    item = form.get(key)
    if item is None or not isinstance(item, UploadFile):
        return None
    fn = (getattr(item, "filename", None) or "").strip()
    return item if fn else None


@router.post("/{version_id}/upload")
async def upload_boot_file(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    from app.http_multipart import read_multipart_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_multipart_form(request, lang=lang)
    file_role = str(form.get("file_role") or "").strip()
    kernel_args = str(form.get("kernel_args") or "").strip()
    redirect_to = str(form.get("redirect_to") or "").strip()
    file = _pick_upload_file(form, "file")
    if not file_role:
        raise HTTPException(400, "file_role requis")
    if not file:
        raise HTTPException(400, "fichier requis")

    user = get_session_user(request)
    version = get_iso_version(db, user, version_id)
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
        be.efi_path           = relative
    elif file_role == "modloop":
        be.modloop_path       = relative
    elif file_role == "custom_ipxe":
        be.custom_ipxe_path   = relative

    if kernel_args:
        be.kernel_args = kernel_args

    be.updated_at = datetime.utcnow()

    if version.status != "ready":
        version.status = "ready"

    db.add(
        Upload(
            filename=safe_name,
            file_type=file_role,
            size=size,
            status="done",
            owner_user_id=user.id,
        )
    )
    db.commit()

    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()

    dest = _safe_redirect_path(redirect_to, "/boot-files")
    return RedirectResponse(dest, status_code=302)


@router.post("/{version_id}/replace-wim")
async def replace_boot_wim(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Remplace uniquement le fichier boot.wim d'une version Windows."""
    redir = _auth(request)
    if redir:
        return redir

    from app.http_multipart import read_multipart_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_multipart_form(request, lang=lang)
    file_boot_wim = _pick_upload_file(form, "file_boot_wim")
    if not file_boot_wim:
        raise HTTPException(400, "boot.wim requis")

    user = get_session_user(request)
    version = get_iso_version(db, user, version_id)
    if not version:
        raise HTTPException(404)
    if version.os_type.boot_type != "windows":
        raise HTTPException(400, "Opération réservée aux versions Windows")

    be = version.boot_entry
    if not be:
        raise HTTPException(404, "Aucun BootEntry pour cette version")

    from app.services.slugify import slugify
    from app.services.windows_boot_paths import (
        boot_wim_path_on_disk,
        rel_under_version,
    )

    version_slug = slugify(version.version_label)
    os_slug = version.os_type.slug
    ver_dir = settings.boot_dir / os_slug / version_slug

    dest = boot_wim_path_on_disk(ver_dir, be.boot_wim_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Sauvegarder l'ancien avant d'écraser
    if dest.exists():
        backup = dest.with_suffix(".wim.bak")
        shutil.copy2(dest, backup)

    # Écrire le nouveau fichier
    content = await file_boot_wim.read()
    dest.write_bytes(content)

    be.boot_wim_path = rel_under_version(dest, os_slug, version_slug)
    if (version.os_type.boot_type or "").lower() == "windows":
        windows_mode = (getattr(version, "windows_mode", None) or "desktop").lower()
        winpe_mode = (getattr(version, "winpe_mode", None) or "master").lower()
        should_regen_winpe = windows_mode == "winpe" and winpe_mode == "master"
        try:
            if should_regen_winpe:
                from app.tasks.jobs import regenerate_winpe_scripts_task

                regenerate_winpe_scripts_task.delay(version.id)
        except Exception:
            pass
    db.commit()

    try:
        from app.tasks.jobs import regenerate_menus_task
        regenerate_menus_task.delay()
    except Exception:
        pass

    return RedirectResponse(f"/isos/{version_id}", status_code=302)


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

    user = get_session_user(request)
    version = get_iso_version(db, user, version_id)
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
