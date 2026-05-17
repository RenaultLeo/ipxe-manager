import errno
import json
import logging
import shutil
from pathlib import Path
from typing import Sequence
from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import is_authenticated
from app.i18n import translate
from app.models.models import OsType, IsoVersion, Upload, BootEntry
from app.services.disk_info import fmt_size
from app.services.os_type_order import sort_os_types_for_ui
from app.templating import templates, template_context
from app.config import settings

router = APIRouter(prefix="/isos")
TEMPLATES = templates
logger = logging.getLogger(__name__)

# Recontrôle disk_usage pendant les gros uploads (multipart sans taille fiable sinon).
_UPLOAD_DISK_POLL_EVERY_BYTES = 64 * 1024 * 1024


def _multipart_part_declared_bytes(upload: object) -> int | None:
    """Taille annoncée d'un champ fichier (``.size`` Starlette ou en-tête part ``Content-Length``)."""
    sz = getattr(upload, "size", None)
    if isinstance(sz, int) and sz > 0:
        return sz
    headers = getattr(upload, "headers", None)
    if headers is not None:
        raw = headers.get("content-length")
        if raw:
            try:
                n = int(raw)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    return None


def _raise_if_free_space_below_reserve(reserve: int, lang: str) -> None:
    if _minimum_free_near_upload_roots() <= reserve:
        raise HTTPException(
            status_code=507,
            detail=translate(lang, "iso.upload.disk_low_reserve"),
        )


def _auth(request: Request):
    if not is_authenticated(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=302)
    return None


def _extract_search_terms_for_ot(ot: OsType) -> list[str]:
    """Noms ou motifs configurés dans ``extract_paths_json`` (affichage upload)."""
    try:
        raw = json.loads(getattr(ot, "extract_paths_json", None) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    # Même liste / ordre que l’extraction plan (linux_manual_*)
    from app.services.os_type_extract_plan import _slot_terms_from_specs_raw

    return _slot_terms_from_specs_raw(raw)


def _os_extract_meta_for_upload(os_types: list[OsType]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for ot in os_types:
        meta[str(ot.id)] = {
            "slug": ot.slug,
            "extract_full": bool(getattr(ot, "extract_full_iso", False)),
            "search_terms": _extract_search_terms_for_ot(ot),
        }
    return meta


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def iso_list(
    request: Request,
    db: Session = Depends(get_db),
    os: str | None = Query(None, description="Slug du type d'OS : onglet pré-sélectionné (ex. windows)."),
):
    redir = _auth(request)
    if redir:
        return redir
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    versions = db.query(IsoVersion).order_by(IsoVersion.created_at.desc()).all()
    slug_set = {ot.slug for ot in os_types}
    raw = (os or "").strip().lower()
    filter_os_slug = raw if raw in slug_set else ""
    return templates.TemplateResponse(
        "isos/index.html",
        template_context(
            request,
            os_types=os_types,
            versions=versions,
            fmt_size=fmt_size,
            filter_os_slug=filter_os_slug,
        ),
    )


# ── Upload form ───────────────────────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
async def upload_form(
    request: Request,
    db: Session = Depends(get_db),
    os: str | None = Query(None, description="Slug du type d'OS présélectionné dans le formulaire."),
):
    redir = _auth(request)
    if redir:
        return redir
    os_types = sort_os_types_for_ui(db.query(OsType).all())
    os_extract_meta = _os_extract_meta_for_upload(os_types)
    slug_set = {ot.slug for ot in os_types}
    raw = (os or "").strip().lower()
    preselect_os_slug = raw if raw in slug_set else ""
    return templates.TemplateResponse(
        "isos/upload.html",
        template_context(
            request,
            os_types=os_types,
            os_extract_meta=os_extract_meta,
            preselect_os_slug=preselect_os_slug,
        ),
    )


@router.get("/upload/check-iso-duplicate")
async def upload_check_iso_duplicate(
    request: Request,
    db: Session = Depends(get_db),
    os_type_id: int = Query(..., ge=1),
    version_label: str = Query(""),
    iso_filename: str = Query(""),
):
    """Indique si un ISO avec ce nom existe déjà pour ce couple (type OS + label)."""
    if not is_authenticated(request):
        raise HTTPException(401)
    vn = Path(iso_filename.replace("\\", "/")).name.strip()
    vl = version_label.strip()
    if not vn or not vl:
        return JSONResponse({"duplicate": False})
    dup = _iso_duplicate_label_and_filename(db, os_type_id, vl, vn)
    return JSONResponse({"duplicate": dup})


def _pick_upload_file(form, key: str):
    """Récupère un UploadFile non vide depuis un formulaire multipart, ou ``None``."""
    from starlette.datastructures import UploadFile

    item = form.get(key)
    if item is None or not isinstance(item, UploadFile):
        return None
    fn = (getattr(item, "filename", None) or "").strip()
    return item if fn else None


def _iso_duplicate_label_and_filename(
    db: Session,
    os_type_id: int,
    version_label: str,
    iso_basename: str,
    *,
    exclude_version_id: int | None = None,
) -> bool:
    """Même OS + même label de version + même nom de fichier ISO (basename)."""
    vl = version_label.casefold()
    bn = iso_basename.casefold()
    q = db.query(IsoVersion).filter(IsoVersion.os_type_id == os_type_id)
    if exclude_version_id is not None:
        q = q.filter(IsoVersion.id != exclude_version_id)
    for row in q.all():
        if row.version_label.casefold() != vl:
            continue
        ip = (row.iso_path or "").strip()
        if not ip:
            continue
        if Path(ip).name.casefold() == bn:
            return True
    return False


def _minimum_free_near_upload_roots() -> int:
    """Espace libre minimal (octets) entre ``iso_root`` et ``http_root`` (donc ``boot``)."""
    frees: list[int] = []
    for raw in (settings.iso_root, settings.http_root):
        p = Path(raw)
        try:
            p.mkdir(parents=True, exist_ok=True)
            frees.append(shutil.disk_usage(p).free)
        except OSError:
            frees.append(0)
    return min(frees) if frees else 0


def _discard_partial_iso_upload(
    *,
    iso_pack_dir: Path | None,
    iso_file: Path | None,
    boot_files_written: Sequence[Path],
) -> None:
    """Efface dossier ISO et fichiers boot écrits pendant un upload incomplet."""
    if iso_file and iso_file.is_file():
        try:
            iso_file.unlink()
        except OSError:
            pass
    if iso_pack_dir and iso_pack_dir.is_dir():
        shutil.rmtree(iso_pack_dir, ignore_errors=True)
    for bf in boot_files_written:
        try:
            if bf.is_file():
                bf.unlink()
        except OSError:
            pass


def _route_linux_manual_file(
    be: BootEntry,
    ot: OsType,
    idx: int,
    term: str,
    rel: str,
) -> dict | None:
    """Affecte kernel/initrd/modloop ou retourne un dict pour ``extra_linux_paths_json``."""
    t_low = term.lower()
    bn = Path(rel.split("/")[-1]).name.lower() if rel else ""
    if idx == 0:
        be.kernel_path = rel
        return None
    if idx == 1:
        be.initrd_path = rel
        return None
    if ot.slug == "alpine" and ("modloop" in t_low or bn.startswith("modloop")):
        if not be.modloop_path:
            be.modloop_path = rel
            return None
    return {"basename": term, "path": rel}


@router.post("/upload")
async def upload_iso(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir

    lang = getattr(request.state, "locale", "fr")
    # Avant multipart : sinon ``await request.form()`` consomme tout le corps (barre progression)
    # avant que les garde‑fous tournent.
    mf0 = _minimum_free_near_upload_roots()
    reserve0 = settings.upload_min_free_bytes
    if mf0 <= reserve0:
        raise HTTPException(
            status_code=507,
            detail=translate(lang, "iso.upload.disk_low_reserve"),
        )
    raw_cl = request.headers.get("content-length")
    if raw_cl:
        try:
            posted = int(raw_cl)
            # Corps ≈ fichier(s) multipart ; pessimiste mais évite tout le flux avant refus.
            if posted > 0 and mf0 <= posted + reserve0:
                raise HTTPException(
                    status_code=507,
                    detail=translate(lang, "iso.upload.disk_low_reserve"),
                )
        except ValueError:
            pass

    form = await request.form()
    try:
        os_type_id = int(form.get("os_type_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Type d'OS invalide")

    version_label = str(form.get("version_label") or "").strip()
    notes = str(form.get("notes") or "").strip()
    kernel_args = str(form.get("kernel_args") or "").strip()

    if not version_label:
        raise HTTPException(400, "Label de version requis")

    os_type = db.query(OsType).get(os_type_id)
    if not os_type:
        raise HTTPException(404, "Type d'OS introuvable")

    file_iso = _pick_upload_file(form, "file")
    iso_safe_name = ""
    if file_iso:
        iso_safe_name = Path(file_iso.filename).name
        ext = Path(iso_safe_name).suffix.lower()
        if ext not in {".iso", ".img", ""}:
            raise HTTPException(400, f"Extension non supportée : {ext}")
        if _iso_duplicate_label_and_filename(db, os_type_id, version_label, iso_safe_name):
            raise HTTPException(
                status_code=409,
                detail=translate(lang, "iso.upload.iso_duplicate_submit"),
            )

    mf = _minimum_free_near_upload_roots()
    reserve = settings.upload_min_free_bytes
    if mf <= reserve:
        raise HTTPException(
            status_code=507,
            detail=translate(lang, "iso.upload.disk_low_reserve"),
        )
    decl = _multipart_part_declared_bytes(file_iso) if file_iso else None
    if isinstance(decl, int) and decl > 0 and mf <= decl + reserve:
        raise HTTPException(
            status_code=507,
            detail=translate(lang, "iso.upload.disk_low_reserve"),
        )

    version = IsoVersion(
        os_type_id=os_type_id,
        version_label=version_label,
        status="uploaded",
        iso_size=0,
        notes=notes,
    )
    db.add(version)
    db.flush()

    from app.services.slugify import slugify
    from starlette.datastructures import UploadFile as StarletteUploadFile

    version_slug = slugify(version.version_label)
    boot_dir = settings.boot_dir / os_type.slug / version_slug

    iso_pack_dir: Path | None = None
    iso_written_path: Path | None = None
    saved_boot_paths: list[Path] = []

    async def save_boot_file(upload: StarletteUploadFile, fname: str) -> str:
        dest_p = boot_dir / fname
        pending_poll = 0
        with open(dest_p, "wb") as f:
            while chunk := await upload.read(1024 * 1024):
                f.write(chunk)
                pending_poll += len(chunk)
                if pending_poll >= _UPLOAD_DISK_POLL_EVERY_BYTES:
                    pending_poll = 0
                    _raise_if_free_space_below_reserve(reserve, lang)
        saved_boot_paths.append(dest_p)
        return f"boot/{os_type.slug}/{version_slug}/{fname}"

    try:
        if file_iso:
            iso_pack_dir = Path(settings.iso_root) / os_type.slug / str(version.id)
            iso_pack_dir.mkdir(parents=True, exist_ok=True)
            dest_iso = iso_pack_dir / iso_safe_name
            iso_written_path = dest_iso
            size_iso = 0
            pending_poll = 0
            with open(dest_iso, "wb") as f:
                while chunk := await file_iso.read(1024 * 1024):
                    f.write(chunk)
                    size_iso += len(chunk)
                    pending_poll += len(chunk)
                    if pending_poll >= _UPLOAD_DISK_POLL_EVERY_BYTES:
                        pending_poll = 0
                        _raise_if_free_space_below_reserve(reserve, lang)
                    if size_iso > settings.max_upload_size:
                        raise HTTPException(413, "Fichier trop volumineux")
            version.iso_path = str(dest_iso)
            version.iso_size = size_iso
            db.add(
                Upload(filename=iso_safe_name, file_type="iso", size=size_iso, status="done")
            )

        boot_dir.mkdir(parents=True, exist_ok=True)
        be = BootEntry(iso_version_id=version.id, kernel_args=kernel_args)
        db.add(be)

        has_boot_files = False
        extra_linux: list[dict] = []

        if os_type.boot_type == "windows":
            file_bcd = _pick_upload_file(form, "file_bcd")
            file_boot_sdi = _pick_upload_file(form, "file_boot_sdi")
            file_boot_wim = _pick_upload_file(form, "file_boot_wim")
            file_bootmgr = _pick_upload_file(form, "file_bootmgr")
            if file_bcd:
                be.bcd_path = await save_boot_file(file_bcd, "BCD")
                has_boot_files = True
            if file_boot_sdi:
                be.boot_sdi_path = await save_boot_file(file_boot_sdi, "boot.sdi")
                has_boot_files = True
            if file_boot_wim:
                be.boot_wim_path = await save_boot_file(file_boot_wim, "boot.wim")
                has_boot_files = True
            if file_bootmgr:
                be.bootmgr_path = await save_boot_file(
                    file_bootmgr, Path(file_bootmgr.filename).name
                )
                has_boot_files = True
        else:
            linux_terms = _extract_search_terms_for_ot(os_type)
            if linux_terms:
                for i, term in enumerate(linux_terms):
                    uf = _pick_upload_file(form, f"linux_manual_{i}")
                    if not uf:
                        continue
                    name_on_disk = Path(uf.filename).name
                    rel = await save_boot_file(uf, name_on_disk)
                    routed = _route_linux_manual_file(be, os_type, i, term, rel)
                    if routed is not None:
                        extra_linux.append(routed)
                    has_boot_files = True
            else:
                file_kernel = _pick_upload_file(form, "file_kernel")
                file_initrd = _pick_upload_file(form, "file_initrd")
                file_modloop = _pick_upload_file(form, "file_modloop")
                if file_kernel:
                    be.kernel_path = await save_boot_file(
                        file_kernel, Path(file_kernel.filename).name
                    )
                    has_boot_files = True
                if file_initrd:
                    be.initrd_path = await save_boot_file(
                        file_initrd, Path(file_initrd.filename).name
                    )
                    has_boot_files = True
                if file_modloop:
                    be.modloop_path = await save_boot_file(
                        file_modloop, Path(file_modloop.filename).name
                    )
                    has_boot_files = True

        if extra_linux:
            be.extra_linux_paths_json = json.dumps(extra_linux, ensure_ascii=False)

        file_custom_ipxe = _pick_upload_file(form, "file_custom_ipxe")
        if file_custom_ipxe:
            be.custom_ipxe_path = await save_boot_file(
                file_custom_ipxe, Path(file_custom_ipxe.filename).name
            )
            has_boot_files = True

        if has_boot_files:
            version.status = "ready"

        db.commit()

    except HTTPException as exc:
        db.rollback()
        _discard_partial_iso_upload(
            iso_pack_dir=iso_pack_dir,
            iso_file=iso_written_path,
            boot_files_written=saved_boot_paths,
        )
        raise exc
    except OSError as exc:
        db.rollback()
        _discard_partial_iso_upload(
            iso_pack_dir=iso_pack_dir,
            iso_file=iso_written_path,
            boot_files_written=saved_boot_paths,
        )
        en = getattr(exc, "errno", None)
        win = getattr(exc, "winerror", None)
        if en == errno.ENOSPC or win == 112:
            raise HTTPException(
                status_code=507,
                detail=translate(lang, "iso.upload.disk_full"),
            ) from exc
        logger.warning("Erreur I/O lors d'un upload ISO : %s", exc)
        raise HTTPException(
            status_code=500,
            detail=translate(lang, "iso.upload.io_error"),
        ) from exc
    except Exception as exc:
        db.rollback()
        _discard_partial_iso_upload(
            iso_pack_dir=iso_pack_dir,
            iso_file=iso_written_path,
            boot_files_written=saved_boot_paths,
        )
        logger.exception("Échec lors d'un upload ISO")
        raise HTTPException(
            status_code=500,
            detail=translate(lang, "iso.upload.io_error"),
        ) from exc

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
    basename_report: dict[str, list[str]] = {}
    raw = getattr(version, "extract_basename_report_json", "") or ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                basename_report = {k: v for k, v in parsed.items() if isinstance(v, list)}
        except (json.JSONDecodeError, TypeError):
            basename_report = {}
    basename_report_items = sorted(basename_report.items(), key=lambda kv: kv[0].lower())
    boot_extra_linux: list[dict] = []
    be_detail = getattr(version, "boot_entry", None)
    if be_detail:
        lx = getattr(be_detail, "extra_linux_paths_json", "") or ""
        if lx.strip():
            try:
                plist = json.loads(lx)
                if isinstance(plist, list):
                    boot_extra_linux = [x for x in plist if isinstance(x, dict) and x.get("path")]
            except (json.JSONDecodeError, TypeError):
                boot_extra_linux = []
    return templates.TemplateResponse(
        "isos/detail.html",
        template_context(
            request,
            version=version,
            fmt_size=fmt_size,
            basename_report=basename_report,
            basename_report_items=basename_report_items,
            boot_extra_linux=boot_extra_linux,
        ),
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
async def iso_status_fragment(
    version_id: int,
    request: Request,
    align: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """HTMX endpoint — retourne uniquement le badge de statut HTML."""
    version = db.query(IsoVersion).get(version_id)
    if not version:
        raise HTTPException(404)
    pull_end = align == "end"
    return templates.TemplateResponse(
        "isos/status_badge.html",
        template_context(
            request,
            status=version.status,
            version_id=version_id,
            pull_end=pull_end,
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

        # 1. ISO : dossier dédié par version (/isos/<os>/<id>/…) ou ancien fichier plat
        iso_slot = Path(settings.iso_root) / os_slug / str(version.id)
        if iso_slot.is_dir():
            shutil.rmtree(iso_slot, ignore_errors=True)
        elif version.iso_path:
            legacy = Path(version.iso_path)
            if legacy.is_file():
                legacy.unlink(missing_ok=True)

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
