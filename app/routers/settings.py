import json
import re
import io
from pathlib import Path, PurePosixPath

from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import auth_redirect_admin, hash_password
from app.models.models import AppSetting, OsType
from app.services.os_type_order import sort_os_types_for_ui
from app.services.menu_generator import MENU_LOGO_UPLOAD_NAME
from app.config import (
    settings as app_settings,
    persist_ipxe_debug,
    persist_server_base_url,
    resolve_ipxe_debug,
    resolve_server_base_url,
)
from app.services.tls_certificates import get_tls_cert_status, renew_tls_certificate
from app.templating import templates, template_context
from app.services.autoconfig_types import all_config_types_for_ui, config_type_labels as _config_type_labels
from app.services.slugify import slugify

BUNDLED_MENU_LOGO = Path(__file__).resolve().parent.parent / "resources" / "default_menu_logo.png"

router = APIRouter(prefix="/settings")

BOOT_TYPE_CHOICES = frozenset({"linux", "windows", "tools", "esxi"})

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}$")
FORCED_AUTOCONFIG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")


class OsTypeReorderBody(BaseModel):
    order: list[int] = Field(..., min_length=1)


def _auth(request: Request):
    return auth_redirect_admin(request)


def _set_setting(db: Session, key: str, value: str):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def _parse_extract_specs(form) -> list[dict]:
    """Saisie « nom de fichier » ou motif fnmatch legacy (si *, ?, [])."""
    cols = form.getlist("extract_file")
    mxs = form.getlist("extract_max")
    out: list[dict] = []
    for i, raw in enumerate(cols):
        s = str(raw).strip()
        if not s:
            continue
        mx_raw = str(mxs[i]).strip() if i < len(mxs) else "1"
        try:
            m = max(1, int(mx_raw))
        except ValueError:
            m = 1
        s_posix = s.replace("\\", "/")
        globs = ("*" in s_posix or "?" in s_posix or ("[" in s_posix and "]" in s_posix))
        if globs:
            out.append({"pattern": s_posix, "max": m})
            continue
        bn = PurePosixPath(s_posix).name
        if bn:
            out.append({"filename": bn, "max": m})
    return out


def _autoconfig_form_context(db: Session, request: Request) -> dict:
    lang = getattr(request.state, "locale", "fr")
    choices = all_config_types_for_ui(db)
    labels = _config_type_labels(lang, choices)
    return {"autoconfig_choices": choices, "autoconfig_labels": labels}


def _parse_forced_autoconfig(form, db: Session) -> tuple[str | None, str]:
    """Retourne (valeur BDD ou None, code erreur vide si ok)."""
    choice = str(form.get("autoconfig_type") or "").strip()
    raw_new = str(form.get("autoconfig_type_new") or "").strip()
    allowed = set(all_config_types_for_ui(db))
    if not choice:
        return None, ""
    if choice == "__new__":
        if not raw_new:
            return None, "autoconfig"
        cand = slugify(raw_new).replace(".", "-")
        if not cand or not FORCED_AUTOCONFIG_SLUG_RE.match(cand):
            return None, "autoconfig"
        return cand, ""
    if choice not in allowed:
        return None, "autoconfig"
    return choice, ""


@router.get("/bundled-menu-logo.png")
async def bundled_menu_logo_png():
    """PNG intégré affiché dans l’aperçu Paramètres quand aucun logo personnalisé n’est en place."""
    if not BUNDLED_MENU_LOGO.is_file():
        raise HTTPException(status_code=404, detail="bundled logo missing")
    return FileResponse(BUNDLED_MENU_LOGO, media_type="image/png")


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    logo_fs = Path(app_settings.http_root) / "menus" / MENU_LOGO_UPLOAD_NAME
    menu_logo_uploaded = logo_fs.is_file()
    menu_logo_qs = int(logo_fs.stat().st_mtime) if menu_logo_uploaded else 0

    current = {
        "server_base_url": resolve_server_base_url(db),
        "ipxe_debug": resolve_ipxe_debug(db),
        "tftp_root": app_settings.tftp_root,
        "http_root": app_settings.http_root,
        "iso_root": app_settings.iso_root,
        "menu_logo_uploaded": menu_logo_uploaded,
        "menu_logo_qs": menu_logo_qs,
        "menu_logo_filename": MENU_LOGO_UPLOAD_NAME,
    }
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    tls = get_tls_cert_status()
    return templates.TemplateResponse(
        "settings.html",
        template_context(request, current=current, os_types=os_types, tls=tls),
    )


@router.get("/os-types/new", response_class=HTMLResponse)
async def os_type_new_get(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    ctx = _autoconfig_form_context(db, request)
    return templates.TemplateResponse(
        "settings/os_type_form.html",
        template_context(
            request,
            ot=None,
            patterns=[{}],
            err=request.query_params.get("err"),
            **ctx,
        ),
    )


@router.post("/os-types/new")
async def os_type_new_post(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    from app.http_multipart import read_multipart_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_multipart_form(request, lang=lang)
    slug = str(form.get("slug") or "").strip().lower()
    label = str(form.get("label") or "").strip()
    boot_type = str(form.get("boot_type") or "linux").strip().lower()
    icon = str(form.get("icon") or "bi-hdd").strip() or "bi-hdd"

    extract_full = str(form.get("extract_full") or "") in ("1", "on", "true")

    patterns = _parse_extract_specs(form)

    err = ""
    if not slug or not SLUG_RE.match(slug):
        err = "slug"
    elif not label:
        err = "label"
    elif boot_type not in BOOT_TYPE_CHOICES:
        err = "boot_type"
    elif db.query(OsType).filter(OsType.slug == slug).first():
        err = "duplicate"
    elif not extract_full and len(patterns) == 0:
        err = "patterns"

    forced_cfg, ferr = _parse_forced_autoconfig(form, db)
    if not err and ferr:
        err = ferr

    if err:
        return RedirectResponse(f"/settings/os-types/new?err={err}", status_code=302)

    max_ord = db.query(func.coalesce(func.max(OsType.ui_sort_order), -1)).scalar()
    next_ord = int(max_ord if max_ord is not None else -1) + 1
    db.add(
        OsType(
            slug=slug,
            label=label,
            icon=icon,
            boot_type=boot_type,
            is_builtin=False,
            ui_sort_order=next_ord,
            show_on_dashboard=True,
            extract_full_iso=extract_full,
            extract_paths_json=json.dumps(patterns),
            forced_autoconfig_type=forced_cfg,
        )
    )
    db.commit()
    return RedirectResponse("/settings", status_code=302)


@router.get("/os-types/{os_id}/edit", response_class=HTMLResponse)
async def os_type_edit_get(os_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    ot = db.query(OsType).get(os_id)
    if not ot:
        raise HTTPException(status_code=404)
    if ot.is_builtin:
        return RedirectResponse("/settings?msg=os_builtin_noedit", status_code=302)
    try:
        patterns = json.loads(ot.extract_paths_json or "[]")
        if not patterns:
            patterns = [{}]
    except json.JSONDecodeError:
        patterns = [{}]
    ctx = _autoconfig_form_context(db, request)
    return templates.TemplateResponse(
        "settings/os_type_form.html",
        template_context(
            request,
            ot=ot,
            patterns=patterns,
            err=request.query_params.get("err"),
            **ctx,
        ),
    )


@router.post("/os-types/{os_id}/edit")
async def os_type_edit_post(os_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    ot = db.query(OsType).get(os_id)
    if not ot:
        raise HTTPException(status_code=404)
    if ot.is_builtin:
        return RedirectResponse("/settings", status_code=302)

    from app.http_multipart import read_multipart_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_multipart_form(request, lang=lang)
    label = str(form.get("label") or "").strip()
    boot_type = str(form.get("boot_type") or "linux").strip().lower()
    icon = str(form.get("icon") or "bi-hdd").strip() or "bi-hdd"

    extract_full = str(form.get("extract_full") or "") in ("1", "on", "true")
    patterns = _parse_extract_specs(form)

    err = ""
    if not label:
        err = "label"
    elif boot_type not in BOOT_TYPE_CHOICES:
        err = "boot_type"
    elif not extract_full and len(patterns) == 0:
        err = "patterns"

    forced_cfg, ferr = _parse_forced_autoconfig(form, db)
    if not err and ferr:
        err = ferr

    if err:
        return RedirectResponse(f"/settings/os-types/{os_id}/edit?err={err}", status_code=302)

    ot.label = label
    ot.boot_type = boot_type
    ot.icon = icon
    ot.extract_full_iso = extract_full
    ot.extract_paths_json = json.dumps(patterns)
    ot.forced_autoconfig_type = forced_cfg
    db.commit()
    return RedirectResponse("/settings", status_code=302)


@router.post("/server-url")
async def update_server_url(
    request: Request,
    server_base_url: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    persist_server_base_url(db, server_base_url)
    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    return RedirectResponse("/settings", status_code=302)


@router.post("/ipxe-debug")
async def update_ipxe_debug(
    request: Request,
    ipxe_debug: str = Form("0"),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    enabled = str(ipxe_debug).strip().lower() in ("1", "on", "true", "yes")
    persist_ipxe_debug(db, enabled)
    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    msg = "ipxe_debug_on" if enabled else "ipxe_debug_off"
    return RedirectResponse(f"/settings?msg={msg}", status_code=302)


@router.post("/tls/renew")
async def renew_tls_cert(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    base_url = resolve_server_base_url(db)
    ok, detail = renew_tls_certificate(base_url)
    if ok:
        return RedirectResponse("/settings?msg=tls_renew_ok", status_code=302)
    if detail == "sudo_denied":
        return RedirectResponse("/settings?msg=tls_renew_sudo", status_code=302)
    if detail == "script_missing":
        return RedirectResponse("/settings?msg=tls_renew_script", status_code=302)
    err = quote(detail[:400], safe="")
    return RedirectResponse(f"/settings?msg=tls_renew_fail&detail={err}", status_code=302)


@router.post("/password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    hashed = hash_password(new_password)
    _set_setting(db, "admin_password_hash", hashed)
    from app.models.models import User

    admin_user = db.query(User).filter(User.username == "admin").first()
    if admin_user:
        admin_user.password_hash = hashed
        db.commit()
    return RedirectResponse("/settings?msg=password_updated", status_code=302)


MENU_LOGO_MAX_BYTES = 3 * 1024 * 1024


@router.post("/menu-logo")
async def menu_logo_post(
    request: Request,
    file: UploadFile = File(...),
):
    redir = _auth(request)
    if redir:
        return redir

    menus_dir = Path(app_settings.http_root) / "menus"
    menus_dir.mkdir(parents=True, exist_ok=True)
    dest = menus_dir / MENU_LOGO_UPLOAD_NAME

    try:
        raw = await file.read()
    except Exception:
        return RedirectResponse("/settings?msg=menu_logo_bad", status_code=302)

    if not raw:
        return RedirectResponse("/settings?msg=menu_logo_bad", status_code=302)
    if len(raw) > MENU_LOGO_MAX_BYTES:
        return RedirectResponse("/settings?msg=menu_logo_big", status_code=302)

    try:
        from PIL import Image
    except ImportError:
        return RedirectResponse("/settings?msg=menu_logo_no_pillow", status_code=302)

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = img.convert("RGBA")
        img.save(dest, "PNG", optimize=True)
    except Exception:
        return RedirectResponse("/settings?msg=menu_logo_bad", status_code=302)

    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    return RedirectResponse("/settings?msg=menu_logo_ok", status_code=302)


@router.post("/menu-logo/delete")
async def menu_logo_delete(request: Request):
    redir = _auth(request)
    if redir:
        return redir

    p = Path(app_settings.http_root) / "menus" / MENU_LOGO_UPLOAD_NAME
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass

    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    return RedirectResponse("/settings?msg=menu_logo_removed", status_code=302)


@router.post("/os-types/reorder")
async def os_types_reorder(
    request: Request,
    body: OsTypeReorderBody,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    rows = db.query(OsType).all()
    all_ids = {r.id for r in rows}
    got = list(body.order)
    if set(got) != all_ids or len(got) != len(all_ids):
        return JSONResponse({"ok": False, "error": "invalid_order"}, status_code=400)
    rank: dict[int, OsType] = {r.id: r for r in rows}
    for idx, oid in enumerate(got):
        rank[oid].ui_sort_order = idx
    db.commit()
    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    return JSONResponse({"ok": True})


@router.post("/os-types/{os_id}/toggle-dashboard")
async def os_type_toggle_dashboard(os_id: int, request: Request, db: Session = Depends(get_db)):
    wants_json = "application/json" in (request.headers.get("accept") or "").lower()
    redir = _auth(request)
    if redir:
        if wants_json:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        return redir
    ot = db.query(OsType).get(os_id)
    if not ot:
        if wants_json:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        raise HTTPException(status_code=404)
    ot.show_on_dashboard = not bool(getattr(ot, "show_on_dashboard", True))
    db.commit()
    vis = bool(ot.show_on_dashboard)
    if wants_json:
        return JSONResponse({"ok": True, "show_on_dashboard": vis})
    return RedirectResponse("/settings", status_code=302)


@router.post("/os-types/{os_id}/delete")
async def delete_os_type(os_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    ot = db.query(OsType).get(os_id)
    if not ot:
        return RedirectResponse("/settings", status_code=302)
    if ot.is_builtin:
        return RedirectResponse("/settings?msg=os_builtin_nodelete", status_code=302)
    db.delete(ot)
    db.commit()
    return RedirectResponse("/settings", status_code=302)
