import json
import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated, hash_password
from app.models.models import AppSetting, OsType
from app.services.os_type_order import sort_os_types_for_ui
from app.services.slugify import slugify
from app.config import settings as app_settings
from app.templating import templates, template_context
from app.routers.configs import all_config_types_for_ui, _config_type_labels

router = APIRouter(prefix="/settings")

EDITABLE_KEYS = ["server_base_url", "admin_password_hash"]

BOOT_TYPE_CHOICES = frozenset({"linux", "windows", "tools"})

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}$")
FORCED_AUTOCONFIG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


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


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    current = {
        "server_base_url": _get_setting(db, "server_base_url", app_settings.server_base_url),
        "tftp_root": app_settings.tftp_root,
        "http_root": app_settings.http_root,
        "iso_root": app_settings.iso_root,
    }
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    return templates.TemplateResponse(
        "settings.html",
        template_context(request, current=current, os_types=os_types),
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
    form = await request.form()
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

    db.add(
        OsType(
            slug=slug,
            label=label,
            icon=icon,
            boot_type=boot_type,
            is_builtin=False,
            extract_full_iso=extract_full,
            extract_paths_json=json.dumps(patterns),
            ipxe_roles_json="[]",
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

    form = await request.form()
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
    ot.ipxe_roles_json = "[]"
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
    _set_setting(db, "server_base_url", server_base_url.rstrip("/"))
    app_settings.server_base_url = server_base_url.rstrip("/")
    from app.tasks.jobs import regenerate_menus_task

    regenerate_menus_task.delay()
    return RedirectResponse("/settings", status_code=302)


@router.post("/password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    _set_setting(db, "admin_password_hash", hash_password(new_password))
    return RedirectResponse("/settings?msg=password_updated", status_code=302)


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
