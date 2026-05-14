import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import templates, template_context


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("", response_class=HTMLResponse)
async def boot_list(request: Request, db: Session = Depends(get_db),
                    scan_result: str = ""):
    redir = _auth(request)
    if redir:
        return redir
    versions = (
        db.query(IsoVersion)
        .filter(IsoVersion.status.in_(["uploaded", "ready", "extracting", "error"]))
        .all()
    )
    return templates.TemplateResponse(
        "boot_files.html",
        template_context(
            request,
            versions=versions,
            fmt_size=fmt_size,
            server_url=settings.server_base_url,
            scan_result=scan_result,
        ),
    )


@router.post("/scan")
async def scan_boot_files(request: Request, db: Session = Depends(get_db)):
    """Scanne boot/ et enregistre les fichiers existants en DB."""
    redir = _auth(request)
    if redir:
        return redir
    from app.services.boot_scanner import scan_and_register
    res = scan_and_register(db)

    # Régénérer les menus avec les nouveaux chemins
    try:
        from app.services.menu_generator import regenerate_all
        regenerate_all(db)
    except Exception:
        pass

    msg = f"Scan terminé — {res['updated']} version(s) mise(s) à jour, {res['skipped']} ignorée(s)"
    if res.get("errors"):
        msg += f", {len(res['errors'])} erreur(s)"
    return RedirectResponse(f"/boot-files?scan_result={msg}", status_code=302)


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

    db.add(Upload(filename=safe_name, file_type=file_role, size=size, status="done"))
    db.commit()

    from app.tasks.jobs import regenerate_menus_task
    regenerate_menus_task.delay()

    return RedirectResponse("/boot-files", status_code=302)


@router.post("/{version_id}/replace-wim")
async def replace_boot_wim(
    version_id: int,
    request: Request,
    file_boot_wim: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Remplace uniquement le fichier boot.wim d'une version Windows."""
    redir = _auth(request)
    if redir:
        return redir

    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)
    if version.os_type.boot_type != "windows":
        raise HTTPException(400, "Opération réservée aux versions Windows")

    be = version.boot_entry
    if not be:
        raise HTTPException(404, "Aucun BootEntry pour cette version")

    from app.services.slugify import slugify
    version_slug = slugify(version.version_label)
    os_slug = version.os_type.slug

    # Trouver le boot.wim existant ou définir un emplacement par défaut
    if be.boot_wim_path:
        dest = Path(settings.http_root) / be.boot_wim_path
    else:
        # Emplacement par défaut : sources/boot.wim (standard Windows)
        dest = settings.boot_dir / os_slug / version_slug / "sources" / "boot.wim"

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Sauvegarder l'ancien avant d'écraser
    if dest.exists():
        backup = dest.with_suffix(".wim.bak")
        shutil.copy2(dest, backup)

    # Écrire le nouveau fichier
    content = await file_boot_wim.read()
    dest.write_bytes(content)

    # Mettre à jour le chemin en base
    rel = f"boot/{os_slug}/{version_slug}/sources/boot.wim"
    be.boot_wim_path = rel
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
