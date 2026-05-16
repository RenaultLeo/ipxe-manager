import json
import re

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated, hash_password
from app.models.models import AppSetting, OsType
from app.services.os_type_order import sort_os_types_for_ui
from app.config import settings as app_settings
from app.templating import templates, template_context

router = APIRouter(prefix="/settings")

EDITABLE_KEYS = ["server_base_url", "admin_password_hash"]

BOOT_TYPE_CHOICES = frozenset({"linux", "windows", "tools"})

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}$")


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


def _parse_extract_patterns(form) -> list[dict]:
    pats = form.getlist("extract_pat")
    mxs = form.getlist("extract_max")
    out: list[dict] = []
    for i, pat in enumerate(pats):
        pat = str(pat).strip()
        if not pat:
            continue
        mx_raw = str(mxs[i]).strip() if i < len(mxs) else "1"
        try:
            m = max(1, int(mx_raw))
        except ValueError:
            m = 1
        out.append({"pattern": pat, "max": m})
    return out


def _parse_ipxe_roles(form) -> list[dict]:
    roles = form.getlist("menu_role")
    mpats = form.getlist("menu_pat")
    mord = form.getlist("menu_order")
    out: list[dict] = []
    for i, role in enumerate(roles):
        role_l = str(role).strip().lower()
        if not role_l:
            continue
        ptn = str(mpats[i]).strip() if i < len(mpats) else ""
        if not ptn:
            continue
        o_raw = str(mord[i]).strip() if i < len(mord) else str(i)
        try:
            o = int(o_raw)
        except ValueError:
            o = i
        out.append({"role": role_l, "path_pattern": ptn, "sort_order": o})
    return sorted(out, key=lambda r: r["sort_order"])


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
    return templates.TemplateResponse(
        "settings/os_type_form.html",
        template_context(
            request,
            ot=None,
            patterns=[{}],
            roles=[{}],
            err=request.query_params.get("err"),
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

    patterns = _parse_extract_patterns(form)
    roles_j = _parse_ipxe_roles(form)

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
            ipxe_roles_json=json.dumps(roles_j),
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
    try:
        roles = json.loads(ot.ipxe_roles_json or "[]")
        if not roles:
            roles = [{}]
    except json.JSONDecodeError:
        roles = [{}]
    return templates.TemplateResponse(
        "settings/os_type_form.html",
        template_context(
            request,
            ot=ot,
            patterns=patterns,
            roles=roles,
            err=request.query_params.get("err"),
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
    patterns = _parse_extract_patterns(form)
    roles_j = _parse_ipxe_roles(form)

    err = ""
    if not label:
        err = "label"
    elif boot_type not in BOOT_TYPE_CHOICES:
        err = "boot_type"
    elif not extract_full and len(patterns) == 0:
        err = "patterns"

    if err:
        return RedirectResponse(f"/settings/os-types/{os_id}/edit?err={err}", status_code=302)

    ot.label = label
    ot.boot_type = boot_type
    ot.icon = icon
    ot.extract_full_iso = extract_full
    ot.extract_paths_json = json.dumps(patterns)
    ot.ipxe_roles_json = json.dumps(roles_j)
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
