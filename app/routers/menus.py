import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import (
    ROLE_ADMIN,
    auth_redirect_admin,
    auth_redirect_login,
    get_session_user,
    is_admin,
    is_authenticated,
)
from app.services.ownership import filter_iso_versions, get_boot_entry
from app.models.models import OsType, IsoVersion, BootEntry, RemoteChain
from app.services.os_type_order import sort_os_types_for_ui
from app.templating import templates, template_context
from app.config import settings, resolve_server_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ipxe-menus")


def _auth(request: Request):
    return auth_redirect_login(request)


def _auth_raw_script(request: Request):
    """Fetch AJAX : 401 si non connecté (évite une page HTML login dans le <pre>)."""
    if is_authenticated(request):
        return None
    if _wants_json(request) or (request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
        raise HTTPException(status_code=401, detail="unauthorized")
    return auth_redirect_login(request)


def _safe_menu_filename(filename: str) -> str:
    name = Path(filename).name
    if name != filename or not name.endswith(".ipxe"):
        raise HTTPException(status_code=400, detail="invalid filename")
    return name


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "").lower()


def _json_or_redirect_unauth(request: Request, redir):
    """Si non authentifié : JSON 401 pour fetch/AJAX, sinon redirection login."""
    if redir is None:
        return None
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return redir


def _not_found_chain_response(request: Request):
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return None


def _collect_menu_files(db: Session | None = None) -> list[dict]:
    base = resolve_server_base_url(db)
    files = []
    if settings.menus_dir.exists():
        for f in sorted(settings.menus_dir.glob("*.ipxe")):
            files.append({
                "name": f.name,
                "url": f"{base}/menus/{f.name}",
                "size": f.stat().st_size,
                "raw_url": f"/ipxe-menus/{f.name}/raw",
            })
    return files


def _collect_custom_scripts(db: Session, request: Request) -> list[dict]:
    """Retourne les BootEntry avec script personnalisé (filtrés par propriétaire)."""
    scripts = []
    user = get_session_user(request)
    q = (
        db.query(BootEntry)
        .filter(BootEntry.custom_ipxe_path.isnot(None))
        .join(BootEntry.iso_version)
        .join(IsoVersion.os_type)
    )
    if user and user.role != ROLE_ADMIN:
        owned = [r[0] for r in filter_iso_versions(db, user).with_entities(IsoVersion.id).all()]
        if not owned:
            return []
        q = q.filter(BootEntry.iso_version_id.in_(owned))
    entries = q.all()
    http_root = Path(settings.http_root)
    base = resolve_server_base_url(db)
    for e in entries:
        path = http_root / e.custom_ipxe_path
        size = path.stat().st_size if path.is_file() else 0
        rel = str(e.custom_ipxe_path).replace("\\", "/").lstrip("/")
        scripts.append({
            "boot_entry_id": e.id,
            "os_label":      e.iso_version.os_type.label,
            "os_slug":       e.iso_version.os_type.slug,
            "version_label": e.iso_version.version_label,
            "filename":      Path(e.custom_ipxe_path).name,
            "rel_path":      e.custom_ipxe_path,
            "url":           f"{base}/{rel}" if rel else base,
            "size":          size,
            "raw_url":       f"/ipxe-menus/custom/{e.id}/raw",
        })
    return scripts


@router.get("", response_class=HTMLResponse)
async def menus_list(
    request: Request,
    db: Session = Depends(get_db),
    tab: str | None = Query(None, description="Onglet pré-ouvert : custom, chains."),
):
    redir = _auth(request)
    if redir:
        return redir

    raw_tab = (tab or "").strip().lower()
    active_tab = raw_tab if raw_tab in ("custom", "chains") else ""

    user = get_session_user(request)
    remote_chains = (
        db.query(RemoteChain).order_by(RemoteChain.id).all() if is_admin(request) else []
    )
    iso_versions = (
        filter_iso_versions(db, user)
        .join(IsoVersion.os_type)
        .options(joinedload(IsoVersion.os_type))
        .order_by(OsType.label.asc(), IsoVersion.version_label.asc())
        .all()
    )
    return templates.TemplateResponse(
        "menus.html",
        template_context(
            request,
            menu_files=_collect_menu_files(db),
            custom_scripts=_collect_custom_scripts(db, request),
            can_edit_global_menus=is_admin(request),
            can_manage_chains=is_admin(request),
            os_types=sort_os_types_for_ui(db.query(OsType).all()),
            iso_versions=iso_versions,
            server_url=resolve_server_base_url(db),
            remote_chains=remote_chains,
            active_tab=active_tab,
        ),
    )


@router.post("/regenerate")
async def regenerate(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    logger.info("Régénération menus planifiée (arrière-plan)")
    return RedirectResponse("/ipxe-menus", status_code=302)


# ── Gestion des scripts personnalisés ─────────────────────────────────────────

@router.post("/custom/{entry_id}/save")
async def custom_script_save(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    from app.http_multipart import form_str, read_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_form(request, lang=lang)
    content = form_str(form, "content")
    if not content:
        raise HTTPException(400, detail="content required")

    user = get_session_user(request)
    entry = get_boot_entry(db, user, entry_id)
    if not entry or not entry.custom_ipxe_path:
        raise HTTPException(404)

    from app.services.filesystem_perms import write_text_file

    path = Path(settings.http_root) / entry.custom_ipxe_path
    write_text_file(path, content, file_mode=0o664)

    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus?tab=custom", status_code=302)


@router.post("/custom/{entry_id}/delete")
async def custom_script_delete(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    user = get_session_user(request)
    entry = get_boot_entry(db, user, entry_id)
    if not entry or not entry.custom_ipxe_path:
        raise HTTPException(404)

    # Supprimer le fichier disque
    path = Path(settings.http_root) / entry.custom_ipxe_path
    if path.exists():
        try:
            path.unlink()
        except Exception:
            logger.exception("Impossible de supprimer %s", path)

    # Effacer le champ en base
    entry.custom_ipxe_path = None
    db.commit()

    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus?tab=custom", status_code=302)


# ── Chainloads distants ───────────────────────────────────────────────────────

def _normalize_chain_url(url: str) -> str:
    u = (url or "").strip()
    if u and "://" not in u:
        u = "http://" + u
    return u


def _probe_chains_status(chains: list[RemoteChain]) -> list[dict]:
    from app.services.server_diagnostics import probe_urls_parallel

    online_map = probe_urls_parallel([(c.id, c.url) for c in chains], timeout=3.0)
    return [{"id": c.id, "online": online_map.get(c.id, False)} for c in chains]


@router.get("/chains/status")
async def chains_status(request: Request, db: Session = Depends(get_db)):
    """État joignable des serveurs distants (sonde async, ne bloque pas le rendu HTML)."""
    redir = auth_redirect_admin(request)
    unauth = _json_or_redirect_unauth(request, redir)
    if unauth is not None:
        return unauth
    chains = db.query(RemoteChain).order_by(RemoteChain.id).all()
    statuses = await asyncio.to_thread(_probe_chains_status, chains)
    return JSONResponse({"ok": True, "chains": statuses})


@router.post("/chains/add")
async def chain_add(
    request: Request,
    db: Session = Depends(get_db),
):
    redir = auth_redirect_admin(request)
    unauth = _json_or_redirect_unauth(request, redir)
    if unauth is not None:
        return unauth

    from app.http_multipart import form_str, read_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_form(request, lang=lang)
    name = form_str(form, "name")
    url = form_str(form, "url")
    if not name or not url:
        raise HTTPException(400, detail="name and url required")

    chain = RemoteChain(name=name, url=_normalize_chain_url(url))
    db.add(chain)
    db.commit()
    db.refresh(chain)
    if _wants_json(request):
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
        return JSONResponse({
            "ok": True,
            "chain": {
                "id": chain.id,
                "name": chain.name,
                "url": chain.url,
                "enabled": chain.enabled,
            },
        })
    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus?tab=chains", status_code=302)


@router.post("/chains/{chain_id}/delete")
async def chain_delete(
    chain_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = auth_redirect_admin(request)
    unauth = _json_or_redirect_unauth(request, redir)
    if unauth is not None:
        return unauth
    chain = db.query(RemoteChain).get(chain_id)
    if not chain:
        nf = _not_found_chain_response(request)
        if nf is not None:
            return nf
        raise HTTPException(404)
    db.delete(chain)
    db.commit()
    if _wants_json(request):
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
        return JSONResponse({"ok": True})
    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus?tab=chains", status_code=302)


@router.post("/chains/{chain_id}/toggle")
async def chain_toggle(
    chain_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = auth_redirect_admin(request)
    unauth = _json_or_redirect_unauth(request, redir)
    if unauth is not None:
        return unauth
    chain = db.query(RemoteChain).get(chain_id)
    if not chain:
        nf = _not_found_chain_response(request)
        if nf is not None:
            return nf
        raise HTTPException(404)
    chain.enabled = not chain.enabled
    db.commit()
    if _wants_json(request):
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
        return JSONResponse({"ok": True, "enabled": chain.enabled})
    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus?tab=chains", status_code=302)


@router.get("/custom/{entry_id}/raw")
async def custom_script_raw(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth_raw_script(request)
    if redir:
        return redir
    user = get_session_user(request)
    entry = get_boot_entry(db, user, entry_id)
    if not entry or not entry.custom_ipxe_path:
        raise HTTPException(404)
    root = Path(settings.http_root).resolve()
    path = (root / entry.custom_ipxe_path).resolve()
    if not path.is_file() or not _is_under_root(path, root):
        raise HTTPException(404)
    return FileResponse(path, media_type="text/plain; charset=utf-8")


@router.get("/{filename}/raw")
async def raw_menu(filename: str, request: Request):
    redir = _auth_raw_script(request)
    if redir:
        return redir
    name = _safe_menu_filename(filename)
    menus_root = settings.menus_dir.resolve()
    f = (menus_root / name).resolve()
    if not f.is_file() or not _is_under_root(f, menus_root):
        raise HTTPException(404)
    return FileResponse(f, media_type="text/plain; charset=utf-8")


@router.post("/{filename}/save")
async def save_menu_override(
    filename: str,
    request: Request,
):
    redir = auth_redirect_admin(request)
    if redir:
        return redir

    from app.http_multipart import form_str, read_form

    lang = getattr(request.state, "locale", "fr")
    form = await read_form(request, lang=lang)
    content = form_str(form, "content")
    if not content:
        raise HTTPException(400, detail="content required")

    f = settings.menus_dir / filename
    if not f.suffix == ".ipxe":
        raise HTTPException(400)
    from app.services.filesystem_perms import prepare_menus_dir, write_text_file

    prepare_menus_dir(settings.menus_dir)
    write_text_file(f, content, file_mode=0o664)
    from app.services.menu_generator import queue_regenerate_all

    queue_regenerate_all()
    return RedirectResponse("/ipxe-menus", status_code=302)
