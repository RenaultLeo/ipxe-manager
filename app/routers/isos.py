import errno
import json
import logging
import shutil
from pathlib import Path
from typing import Sequence
from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from app.database import get_db
from app.auth import auth_redirect_login, get_session_user
from app.services.ownership import can_modify_iso_version, get_iso_version, get_iso_version_view
from app.i18n import translate
from app.models.models import OsType, IsoVersion, Upload, BootEntry, AutoConfig, WinpeInstall
from app.services.disk_info import fmt_size
from app.services.menu_generator import ALPINE_REPO_DEFAULT_PUBLIC
from app.services.os_type_order import sort_os_types_for_ui
from app.templating import templates, template_context
from app.config import settings

router = APIRouter(prefix="/isos")
logger = logging.getLogger(__name__)


class PurgeIsoPreferenceBody(BaseModel):
    enabled: bool


class UbuntuNfsBootBody(BaseModel):
    enabled: bool

# Recontrôle disk_usage pendant les gros uploads (multipart sans taille fiable sinon).
_UPLOAD_DISK_POLL_EVERY_BYTES = 64 * 1024 * 1024


def _multipart_upload_blocked(
    lang: str,
    *,
    mf: int | None = None,
    expected_file_bytes: int = 0,
    multipart_total_upper: int | None = None,
) -> tuple[int, str] | None:
    """
    Règle unique espace vs réserve + taille cumulée (fichiers / corps POST si connu).
    Retourne ``(status_code, detail traduit)`` ou ``None``.
    """
    frees = mf if mf is not None else _minimum_free_near_upload_roots()
    reserve = settings.upload_min_free_bytes
    mx = settings.max_upload_size
    merged_need = expected_file_bytes
    if multipart_total_upper is not None and multipart_total_upper > 0:
        merged_need = max(merged_need, multipart_total_upper)

    if frees <= reserve:
        return (507, translate(lang, "iso.upload.disk_low_reserve"))
    if merged_need > mx:
        return (413, translate(lang, "iso.upload.too_large"))
    if merged_need > 0 and frees <= merged_need + reserve:
        return (507, translate(lang, "iso.upload.disk_low_reserve"))
    return None


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


def _raise_if_free_space_below_reserve(lang: str) -> None:
    blocked = _multipart_upload_blocked(lang)
    if blocked:
        raise HTTPException(status_code=blocked[0], detail=blocked[1])


def _auth(request: Request):
    return auth_redirect_login(request)


def _get_version_view_or_404(db: Session, request: Request, version_id: int) -> IsoVersion:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401)
    version = (
        db.query(IsoVersion)
        .options(
            joinedload(IsoVersion.os_type),
            joinedload(IsoVersion.boot_entry),
            joinedload(IsoVersion.autoconfigs),
            joinedload(IsoVersion.winpe_installs),
        )
        .filter(IsoVersion.id == version_id)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404)
    return version


def _get_version_status_or_404(db: Session, request: Request, version_id: int) -> str:
    """Statut seul (évite joinedload pour le polling HTMX)."""
    from app.services.ownership import get_iso_version_view

    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401)
    version = get_iso_version_view(db, user, version_id)
    if not version:
        raise HTTPException(status_code=404)
    return version.status


def _get_version_or_404(db: Session, request: Request, version_id: int) -> IsoVersion:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401)
    version = get_iso_version(db, user, version_id)
    if not version:
        raise HTTPException(status_code=403, detail="forbidden")
    return version


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
    versions = (
        db.query(IsoVersion)
        .options(joinedload(IsoVersion.os_type))
        .order_by(IsoVersion.created_at.desc())
        .all()
    )
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
    if _auth(request):
        raise HTTPException(401)
    vn = Path(iso_filename.replace("\\", "/")).name.strip()
    vl = version_label.strip()
    if not vn or not vl:
        return JSONResponse({"duplicate": False})
    dup = _iso_duplicate_label_and_filename(db, os_type_id, vl, vn)
    return JSONResponse({"duplicate": dup})


@router.get("/upload/precheck")
async def upload_precheck(
    request: Request,
    total_bytes: int = Query(
        0,
        ge=0,
        le=1_099_511_627_776,  # 1 TiB plafond sur le query param (évite abus URL)
        description="Somme des tailles des fichiers choisis dans le formulaire (approximatif multipart).",
    ),
):
    """
    Vérification avant le POST multipart : espace vs réserve + taille cumulée, sans recevoir les octets fichiers.
    Le JS appelle cet endpoint puis n’affiche la barre de progression que si ``ok``.
    """
    if _auth(request):
        raise HTTPException(401)
    lang = getattr(request.state, "locale", "fr")
    blocked = _multipart_upload_blocked(lang, mf=None, expected_file_bytes=total_bytes)
    if blocked:
        _, detail = blocked
        return JSONResponse({"ok": False, "detail": detail})
    return JSONResponse({"ok": True})


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


def _normalize_alpine_repo_url(lang: str, raw: str) -> str:
    """Valide une URL de dépôt APK Alpine (HTTPS/HTTP uniquement)."""
    s = (raw or "").strip()
    if not s:
        raise HTTPException(400, translate(lang, "iso.upload.alpine_repo_required"))
    if len(s) > 512:
        raise HTTPException(400, translate(lang, "iso.upload.alpine_repo_too_long"))
    if "\n" in s or "\r" in s or "\t" in s:
        raise HTTPException(400, translate(lang, "iso.upload.alpine_repo_invalid"))
    low = s.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise HTTPException(400, translate(lang, "iso.upload.alpine_repo_invalid"))
    return s


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
    # Avant ``await request.form()`` : évite consommer tout le corps avant garde‑fous.
    mf0 = _minimum_free_near_upload_roots()
    posted_ul: int | None = None
    raw_cl = request.headers.get("content-length")
    if raw_cl:
        try:
            n = int(raw_cl)
            if n > 0:
                posted_ul = n
        except ValueError:
            pass

    blocked0 = _multipart_upload_blocked(
        lang,
        mf=mf0,
        expected_file_bytes=0,
        multipart_total_upper=posted_ul,
    )
    if blocked0:
        raise HTTPException(status_code=blocked0[0], detail=blocked0[1])

    form = await request.form()
    try:
        os_type_id = int(form.get("os_type_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Type d'OS invalide")

    version_label = str(form.get("version_label") or "").strip()
    notes = str(form.get("notes") or "").strip()
    kernel_args = str(form.get("kernel_args") or "").strip()
    alpine_repo_custom_raw = form.get("alpine_repo_custom")
    alpine_repo_url_raw = str(form.get("alpine_repo_url") or "").strip()

    if not version_label:
        raise HTTPException(400, "Label de version requis")

    owner = get_session_user(request)
    if not owner:
        raise HTTPException(status_code=401)

    os_type = db.query(OsType).get(os_type_id)
    if not os_type:
        raise HTTPException(404, "Type d'OS introuvable")

    ubuntu_variant = "desktop"
    if os_type.slug == "ubuntu":
        uv = str(form.get("ubuntu_variant") or "desktop").strip().lower()
        if uv not in ("desktop", "server"):
            raise HTTPException(400, translate(lang, "iso.upload.ubuntu_variant_invalid"))
        ubuntu_variant = uv

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

    mf1 = _minimum_free_near_upload_roots()
    decl = _multipart_part_declared_bytes(file_iso) if file_iso else None
    decl_need = decl if isinstance(decl, int) and decl > 0 else 0
    blocked1 = _multipart_upload_blocked(lang, mf=mf1, expected_file_bytes=decl_need)
    if blocked1:
        raise HTTPException(status_code=blocked1[0], detail=blocked1[1])

    version = IsoVersion(
        os_type_id=os_type_id,
        owner_user_id=owner.id,
        version_label=version_label,
        status="uploaded",
        iso_size=0,
        notes=notes,
        ubuntu_variant=ubuntu_variant,
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
                    _raise_if_free_space_below_reserve(lang)
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
                        _raise_if_free_space_below_reserve(lang)
                    if size_iso > settings.max_upload_size:
                        raise HTTPException(413, "Fichier trop volumineux")
            version.iso_path = str(dest_iso)
            version.iso_size = size_iso
            db.add(
                Upload(
                    filename=iso_safe_name,
                    file_type="iso",
                    size=size_iso,
                    status="done",
                    owner_user_id=owner.id,
                )
            )

        boot_dir.mkdir(parents=True, exist_ok=True)
        be = BootEntry(iso_version_id=version.id, kernel_args=kernel_args)
        if os_type.slug == "alpine":
            alpine_custom_on = str(alpine_repo_custom_raw or "").strip().lower() in (
                "1",
                "on",
                "true",
                "yes",
            )
            if alpine_custom_on:
                be.alpine_repo_url = _normalize_alpine_repo_url(lang, alpine_repo_url_raw)
            else:
                be.alpine_repo_url = None
        if os_type.slug == "fedora":
            be.live_os = str(form.get("fedora_live_os") or "").strip().lower() in (
                "1",
                "on",
                "true",
                "yes",
            )
        db.add(be)

        has_boot_files = False
        extra_linux: list[dict] = []

        if os_type.boot_type == "windows":
            file_bcd = _pick_upload_file(form, "file_bcd")
            file_boot_sdi = _pick_upload_file(form, "file_boot_sdi")
            file_bootmgr = _pick_upload_file(form, "file_bootmgr")
            if file_bcd:
                be.bcd_path = await save_boot_file(file_bcd, "BCD")
                has_boot_files = True
            if file_boot_sdi:
                be.boot_sdi_path = await save_boot_file(file_boot_sdi, "boot.sdi")
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
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()

    return RedirectResponse(f"/isos/{version.id}", status_code=302)


def _last_extraction_upload(db: Session, version_id: int) -> Upload | None:
    return (
        db.query(Upload)
        .filter(
            Upload.iso_version_id == version_id,
            Upload.file_type == "extraction",
        )
        .order_by(Upload.created_at.desc())
        .first()
    )


def _last_extract_error_msg(db: Session, version: IsoVersion) -> str:
    """Dernier message d'échec Celery (table uploads, type extraction)."""
    row = _last_extraction_upload(db, version.id)
    if not row or row.status != "error":
        return ""
    return (row.error_msg or "").strip()


def _last_extract_warning_msg(db: Session, version: IsoVersion) -> str:
    """Avertissement après extraction OK (ex. menus non régénérés)."""
    row = _last_extraction_upload(db, version.id)
    if not row or row.status != "done":
        return ""
    msg = (row.error_msg or "").strip()
    if not msg or "menus iPXE" not in msg:
        return ""
    return msg


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{version_id}", response_class=HTMLResponse)
async def iso_detail(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    version = _get_version_view_or_404(db, request, version_id)
    can_modify = can_modify_iso_version(get_session_user(request), version)
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
    iso_raw = (version.iso_path or "").strip()
    iso_file_exists = bool(iso_raw and Path(iso_raw).is_file())
    if (
        can_modify
        and not iso_file_exists
        and getattr(version, "delete_iso_after_next_extract", False)
    ):
        version.delete_iso_after_next_extract = False
        db.add(version)
        db.commit()

    active_autoconfig = None
    published_boot_path = ""
    proxmox_netboot_iso_path = ""
    proxmox_netboot_autoinstall_iso_path = ""
    active_id = getattr(version, "active_autoconfig_id", None)
    if active_id:
        active_autoconfig = next(
            (a for a in (version.autoconfigs or []) if a.id == active_id),
            None,
        )
        if active_autoconfig:
            if version.os_type.slug == "ubuntu":
                try:
                    from app.services.autoconfig_publish import published_seed_dir_rel_path

                    published_boot_path = published_seed_dir_rel_path(version)
                except Exception:
                    published_boot_path = ""
            elif version.os_type.slug == "proxmox" and version.boot_entry:
                try:
                    from app.services.proxmox_autoinstall import (
                        netboot_autoinstall_iso_path,
                        netboot_iso_path,
                    )

                    nb = netboot_iso_path(version)
                    if nb:
                        proxmox_netboot_iso_path = str(
                            nb.relative_to(settings.http_root)
                        ).replace("\\", "/")
                    na = netboot_autoinstall_iso_path(version)
                    if na.is_file():
                        proxmox_netboot_autoinstall_iso_path = str(
                            na.relative_to(settings.http_root)
                        ).replace("\\", "/")
                except Exception:
                    proxmox_netboot_iso_path = ""
                    proxmox_netboot_autoinstall_iso_path = ""

    extract_error_msg = ""
    extract_warning_msg = ""
    if version.status == "error":
        extract_error_msg = _last_extract_error_msg(db, version)
    else:
        extract_warning_msg = _last_extract_warning_msg(db, version)

    winpe_smb_host = ""
    winpe_smb_share = ""
    winpe_installs_root = ""
    if version.os_type.boot_type == "windows":
        try:
            from app.services.winpe_installs import (
                installs_root,
                smb_host_from_settings,
                smb_share_name,
            )

            winpe_smb_host = smb_host_from_settings()
            winpe_smb_share = smb_share_name()
            winpe_installs_root = str(installs_root(version))
        except Exception:
            pass

    winpe_drivers_catalog: list = []
    winpe_drivers_root = ""
    winpe_scripts_dir = ""
    if version.os_type.boot_type == "windows":
        try:
            from app.services.winpe_drivers import (
                catalog_for_template,
                drivers_root,
            )

            winpe_drivers_catalog = catalog_for_template()
            winpe_drivers_root = str(drivers_root())
            from app.services.winpe_scripts import scripts_dir

            winpe_scripts_dir = str(scripts_dir(version))
        except Exception:
            pass

    return templates.TemplateResponse(
        "isos/detail.html",
        template_context(
            request,
            version=version,
            active_autoconfig=active_autoconfig,
            winpe_smb_host=winpe_smb_host,
            winpe_smb_share=winpe_smb_share,
            winpe_installs_root=winpe_installs_root,
            winpe_drivers_catalog=winpe_drivers_catalog,
            winpe_drivers_root=winpe_drivers_root,
            winpe_scripts_dir=winpe_scripts_dir,
            extract_error_msg=extract_error_msg,
            extract_warning_msg=extract_warning_msg,
            fmt_size=fmt_size,
            basename_report=basename_report,
            basename_report_items=basename_report_items,
            boot_extra_linux=boot_extra_linux,
            iso_file_exists=iso_file_exists,
            alpine_repo_default_public=ALPINE_REPO_DEFAULT_PUBLIC,
            can_modify=can_modify,
            published_boot_path=published_boot_path,
            proxmox_netboot_iso_path=proxmox_netboot_iso_path,
            proxmox_netboot_autoinstall_iso_path=proxmox_netboot_autoinstall_iso_path,
        ),
    )


@router.post("/{version_id}/activate-config")
async def iso_activate_config(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    config_id: int = Form(...),
):
    """Ubuntu Desktop : publie cloud-init. Proxmox : crée proxmox-netboot-autoinstall.iso."""
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404)
    user = get_session_user(request)
    if not can_modify_iso_version(user, version):
        raise HTTPException(403)

    cfg = (
        db.query(AutoConfig)
        .filter(
            AutoConfig.id == config_id,
            AutoConfig.iso_version_id == version.id,
        )
        .first()
    )
    if not cfg:
        raise HTTPException(404, detail=translate(lang, "iso.active_config_not_found"))

    os_slug = (version.os_type.slug or "").lower()

    if os_slug == "ubuntu":
        if (getattr(version, "ubuntu_variant", None) or "desktop").lower() == "server":
            raise HTTPException(400, detail=translate(lang, "iso.active_config_desktop_only"))
        try:
            from app.services.autoconfig_publish import activate_ubuntu_config

            activate_ubuntu_config(db, version, cfg)
            from app.services.menu_generator import queue_regenerate_all

            queue_regenerate_all()
        except FileNotFoundError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return RedirectResponse(f"/isos/{version_id}?msg=active_config_ok", status_code=302)

    if os_slug == "proxmox":
        if cfg.config_type != "proxmox-answer":
            raise HTTPException(
                400, detail=translate(lang, "iso.proxmox_active_config_bad_type")
            )
        if not version.boot_entry:
            raise HTTPException(
                400, detail=translate(lang, "iso.proxmox_inject_need_extract")
            )
        try:
            from app.services.proxmox_autoinstall import queue_proxmox_inject

            upload = queue_proxmox_inject(db, version, cfg)
        except FileNotFoundError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, detail=str(exc)) from exc
        return RedirectResponse(
            f"/isos/{version_id}?msg=proxmox_inject_started&upload_id={upload.id}",
            status_code=302,
        )

    raise HTTPException(400, detail=translate(lang, "iso.active_config_not_ubuntu"))


@router.post("/{version_id}/clear-active-config")
async def iso_clear_active_config(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404)
    user = get_session_user(request)
    if not can_modify_iso_version(user, version):
        raise HTTPException(403)

    os_slug = (version.os_type.slug or "").lower()
    if os_slug == "ubuntu":
        from app.services.autoconfig_publish import clear_active_ubuntu_publish

        clear_active_ubuntu_publish(db, version)
    elif os_slug == "proxmox":
        from app.services.autoconfig_publish import clear_active_proxmox_publish

        clear_active_proxmox_publish(db, version)
    if version.status == "ready":
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
    return RedirectResponse(f"/isos/{version_id}", status_code=302)


@router.post("/{version_id}/alpine-repo")
async def iso_alpine_repo_save(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    alpine_repo_custom: str | None = Form(None),
    alpine_repo_url: str = Form(""),
):
    """Met à jour l’URL du dépôt APK Alpine (menus : paramètre ``alpine_repo=`` sur la ligne kernel)."""
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404, "Version introuvable")
    if getattr(version.os_type, "slug", "") != "alpine":
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.alpine_repo_not_alpine"),
        )
    be = getattr(version, "boot_entry", None)
    if not be:
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.alpine_repo_no_boot"),
        )
    custom_on = str(alpine_repo_custom or "").strip().lower() in (
        "1",
        "on",
        "true",
        "yes",
    )
    if custom_on:
        be.alpine_repo_url = _normalize_alpine_repo_url(lang, alpine_repo_url)
    else:
        be.alpine_repo_url = None
    db.add(be)
    db.commit()
    if version.status == "ready":
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
    return RedirectResponse(f"/isos/{version_id}", status_code=302)


@router.post("/{version_id}/fedora-live")
async def iso_fedora_live_save(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    fedora_live_os: str | None = Form(None),
):
    """Active ou non le boot Fedora Live (``root=live:…/LiveOS/squashfs.img`` au lieu de ``inst.stage2``)."""
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404, "Version introuvable")
    if getattr(version.os_type, "slug", "") != "fedora":
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.fedora_live_not_fedora"),
        )
    be = getattr(version, "boot_entry", None)
    if not be:
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.fedora_live_no_boot"),
        )
    be.live_os = str(fedora_live_os or "").strip().lower() in (
        "1",
        "on",
        "true",
        "yes",
    )
    db.add(be)
    db.commit()
    if version.status == "ready":
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
    return RedirectResponse(f"/isos/{version_id}", status_code=302)


@router.post("/{version_id}/ubuntu-nfs-boot")
async def set_ubuntu_nfs_boot(
    version_id: int,
    request: Request,
    body: UbuntuNfsBootBody,
    db: Session = Depends(get_db),
):
    """Active ou non le boot NFS casper pour cette version Ubuntu (ligne kernel du menu généré)."""
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if getattr(version.os_type, "slug", "") != "ubuntu":
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.ubuntu_nfs_not_ubuntu"),
        )
    version.ubuntu_nfs_boot = bool(body.enabled)
    db.add(version)
    db.commit()
    if version.status == "ready":
        from app.services.menu_generator import queue_regenerate_all

        queue_regenerate_all()
    return JSONResponse({"ok": True, "enabled": version.ubuntu_nfs_boot})


@router.post("/{version_id}/purge-iso-preference")
async def set_purge_iso_preference(
    version_id: int,
    request: Request,
    body: PurgeIsoPreferenceBody,
    db: Session = Depends(get_db),
):
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if body.enabled:
        iso_p = Path((version.iso_path or "").strip())
        if not iso_p.is_file():
            raise HTTPException(
                status_code=400,
                detail=translate(lang, "iso.purge_iso_toggle_unavailable"),
            )
    version.delete_iso_after_next_extract = body.enabled
    db.add(version)
    db.commit()
    return JSONResponse({"ok": True, "enabled": body.enabled})


# ── Extract ───────────────────────────────────────────────────────────────────

@router.post("/{version_id}/extract")
async def extract(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404)

    iso_p = Path((version.iso_path or "").strip())
    if not iso_p.is_file():
        raise HTTPException(
            status_code=400,
            detail=translate(lang, "iso.extract_missing_iso"),
        )

    owner = get_session_user(request)
    upload_log = Upload(
        filename=iso_p.name,
        file_type="extraction",
        size=version.iso_size,
        status="pending",
        owner_user_id=owner.id if owner else version.owner_user_id,
        iso_version_id=version_id,
    )
    db.add(upload_log)
    db.commit()

    from app.tasks.jobs import extract_iso_task
    extract_iso_task.delay(version_id, upload_log.id)

    return RedirectResponse(f"/isos/{version_id}", status_code=302)


# ── Job status (HTMX polling) ─────────────────────────────────────────────────

@router.get("/{version_id}/status-fragment", response_class=HTMLResponse)
async def iso_status_fragment(
    version_id: int,
    request: Request,
    align: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """HTMX endpoint — retourne uniquement le badge de statut HTML."""
    status = _get_version_status_or_404(db, request, version_id)
    pull_end = align == "end"
    return templates.TemplateResponse(
        "isos/status_badge.html",
        template_context(
            request,
            status=status,
            version_id=version_id,
            pull_end=pull_end,
        ),
    )


# ── WinPE — install.wim + startnet.cmd ────────────────────────────────────────

def _get_version_modify_with_os(
    db: Session, request: Request, version_id: int
) -> IsoVersion:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401)
    version = (
        db.query(IsoVersion)
        .options(joinedload(IsoVersion.os_type))
        .filter(IsoVersion.id == version_id)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404)
    if not can_modify_iso_version(user, version):
        raise HTTPException(status_code=403, detail="forbidden")
    return version


def _winpe_windows_version(version: IsoVersion, lang: str) -> None:
    if not getattr(version, "os_type", None):
        raise HTTPException(status_code=404)
    if (version.os_type.boot_type or "").lower() != "windows":
        raise HTTPException(400, detail=translate(lang, "iso.winpe_not_windows"))


@router.get("/uploads/{upload_id}/status")
async def upload_job_status(
    upload_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """État d'un upload (install.wim, extraction, etc.) — polling HTMX / XHR."""
    if _auth(request):
        raise HTTPException(401)
    from app.services.ownership import get_upload

    user = get_session_user(request)
    row = get_upload(db, user, upload_id)
    if not row:
        raise HTTPException(404)
    return JSONResponse(
        {
            "status": row.status,
            "error_msg": (row.error_msg or "").strip(),
            "size": row.size or 0,
        }
    )


@router.get("/{version_id}/winpe-drivers/catalog")
async def winpe_drivers_catalog_route(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Catalogue drivers.json (types de machine, chemins, nombre de fichiers)."""
    if _auth(request):
        raise HTTPException(401)
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_view_or_404(db, request, version_id)
    _winpe_windows_version(version, lang)
    from app.services.winpe_drivers import catalog_for_template

    return JSONResponse({"machines": catalog_for_template()})


@router.post("/{version_id}/winpe-drivers/upload")
async def upload_winpe_drivers(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    machine_kind: str = Form(...),
    machine_key: str = Form(""),
    new_machine_name: str = Form(""),
    driver_files: list[UploadFile] = File(...),
):
    """Upload multiple pilotes dans boot/drivers/<type>/ (catalogue drivers.json)."""
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_modify_with_os(db, request, version_id)
    _winpe_windows_version(version, lang)

    files = [f for f in (driver_files or []) if f and (f.filename or "").strip()]
    if not files:
        raise HTTPException(400, detail=translate(lang, "iso.winpe_drivers_no_files"))

    mf0 = _minimum_free_near_upload_roots()
    total_decl = 0
    for f in files:
        decl = _multipart_part_declared_bytes(f)
        if isinstance(decl, int) and decl > 0:
            total_decl += decl
    blocked = _multipart_upload_blocked(lang, mf=mf0, expected_file_bytes=total_decl)
    if blocked:
        raise HTTPException(status_code=blocked[0], detail=blocked[1])

    from app.services.winpe_drivers import (
        rebuild_catalog,
        resolve_machine_upload,
        save_uploaded_driver_files,
    )

    try:
        label, slug, folder = resolve_machine_upload(
            machine_kind=machine_kind,
            machine_key=machine_key,
            new_machine_name=new_machine_name,
        )
        n_saved, names = await save_uploaded_driver_files(folder, files)
        if n_saved == 0:
            raise HTTPException(400, detail=translate(lang, "iso.winpe_drivers_no_files"))
        cat = rebuild_catalog()
        meta = cat.get(label) or {}
        payload = {
            "ok": True,
            "label": label,
            "slug": slug,
            "path": meta.get("path") or f"drivers/{slug}",
            "count": int(meta.get("count") or 0),
            "saved": names,
            "redirect": f"/isos/{version_id}?msg=winpe_drivers_uploaded",
        }
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            raise HTTPException(
                507, detail=translate(lang, "iso.upload.disk_full")
            ) from exc
        logger.warning("Erreur I/O upload drivers WinPE : %s", exc)
        raise HTTPException(
            500, detail=translate(lang, "iso.upload.io_error")
        ) from exc

    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JSONResponse(payload)
    return RedirectResponse(payload["redirect"], status_code=302)


@router.get("/{version_id}/winpe-installs/precheck")
async def winpe_install_precheck(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    total_bytes: int = Query(0, ge=0, le=1_099_511_627_776),
):
    """Vérifie l'espace disque avant upload install.wim (comme /isos/upload/precheck)."""
    if _auth(request):
        raise HTTPException(401)
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_view_or_404(db, request, version_id)
    _winpe_windows_version(version, lang)
    blocked = _multipart_upload_blocked(lang, mf=None, expected_file_bytes=total_bytes)
    if blocked:
        _, detail = blocked
        return JSONResponse({"ok": False, "detail": detail})
    return JSONResponse({"ok": True})


@router.post("/{version_id}/winpe-installs")
async def upload_winpe_install(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    install_slug: str = Form(...),
    install_label: str = Form(""),
    wim_index: int = Form(1),
    file_install_wim: UploadFile = File(...),
):
    """
    Reçoit install.wim (flux HTTP + barre de progression côté client),
    écrit en .part puis finalise en arrière-plan via Celery (comme ISO → extract).
    """
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    user = get_session_user(request)
    if not user:
        raise HTTPException(401)

    mf0 = _minimum_free_near_upload_roots()
    posted_ul: int | None = None
    raw_cl = request.headers.get("content-length")
    if raw_cl:
        try:
            n = int(raw_cl)
            if n > 0:
                posted_ul = n
        except ValueError:
            pass
    blocked0 = _multipart_upload_blocked(
        lang,
        mf=mf0,
        expected_file_bytes=0,
        multipart_total_upper=posted_ul,
    )
    if blocked0:
        raise HTTPException(status_code=blocked0[0], detail=blocked0[1])

    version = _get_version_modify_with_os(db, request, version_id)
    _winpe_windows_version(version, lang)

    from app.services.winpe_installs import (
        INSTALL_WIM_FILENAME,
        install_folder,
        normalize_install_slug,
    )

    try:
        slug = normalize_install_slug(install_slug)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    fname = Path(file_install_wim.filename or "").name
    if not fname.lower().endswith(".wim"):
        raise HTTPException(400, detail=translate(lang, "iso.winpe_wim_only"))

    decl = _multipart_part_declared_bytes(file_install_wim)
    decl_need = decl if isinstance(decl, int) and decl > 0 else 0
    blocked1 = _multipart_upload_blocked(
        lang, mf=_minimum_free_near_upload_roots(), expected_file_bytes=decl_need
    )
    if blocked1:
        raise HTTPException(status_code=blocked1[0], detail=blocked1[1])

    folder = install_folder(version, slug)
    folder.mkdir(parents=True, exist_ok=True)
    part_path = folder / f"{INSTALL_WIM_FILENAME}.part"
    label = (install_label or slug).strip() or slug
    wim_idx = max(1, wim_index)

    upload_log = Upload(
        filename=f"{slug}/{INSTALL_WIM_FILENAME}",
        file_type="install_wim",
        size=0,
        status="pending",
        owner_user_id=user.id,
    )
    db.add(upload_log)
    db.flush()

    size = 0
    pending_poll = 0
    try:
        if part_path.is_file():
            part_path.unlink()
        with open(part_path, "wb") as out:
            while chunk := await file_install_wim.read(1024 * 1024):
                out.write(chunk)
                size += len(chunk)
                pending_poll += len(chunk)
                if pending_poll >= _UPLOAD_DISK_POLL_EVERY_BYTES:
                    pending_poll = 0
                    blocked = _multipart_upload_blocked(
                        lang,
                        mf=_minimum_free_near_upload_roots(),
                        expected_file_bytes=0,
                    )
                    if blocked:
                        raise HTTPException(
                            status_code=blocked[0], detail=blocked[1]
                        )
        upload_log.size = size
        upload_log.status = "processing"
        db.commit()

        from app.tasks.jobs import upload_winpe_install_task

        upload_winpe_install_task.delay(
            upload_log.id,
            version.id,
            slug,
            label,
            wim_idx,
            str(part_path.resolve()),
        )
    except HTTPException:
        db.rollback()
        try:
            if part_path.is_file():
                part_path.unlink()
        except OSError:
            pass
        raise
    except OSError as exc:
        db.rollback()
        try:
            if part_path.is_file():
                part_path.unlink()
        except OSError:
            pass
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            raise HTTPException(
                507, detail=translate(lang, "iso.upload.disk_full")
            ) from exc
        logger.warning("Erreur I/O upload install.wim : %s", exc)
        raise HTTPException(
            500, detail=translate(lang, "iso.upload.io_error")
        ) from exc
    except Exception as exc:
        db.rollback()
        try:
            if part_path.is_file():
                part_path.unlink()
        except OSError:
            pass
        logger.exception("Échec réception install.wim")
        raise HTTPException(
            500, detail=translate(lang, "iso.upload.io_error")
        ) from exc

    redirect_url = f"/isos/{version_id}?msg=winpe_upload_started&upload_id={upload_log.id}"
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JSONResponse(
            {
                "ok": True,
                "upload_id": upload_log.id,
                "redirect": redirect_url,
            }
        )
    return RedirectResponse(redirect_url, status_code=302)


@router.post("/{version_id}/regenerate-winpe-scripts")
async def regenerate_winpe_scripts_route(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Régénère masters.json, deploy.ps1, inject-drivers.ps1 et startnet.cmd dans boot.wim."""
    redir = _auth(request)
    if redir:
        return redir
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_modify_with_os(db, request, version_id)
    _winpe_windows_version(version, lang)

    if not version.winpe_installs:
        raise HTTPException(400, detail=translate(lang, "iso.winpe_scripts_no_masters"))

    if not version.boot_entry or not (version.boot_entry.boot_wim_path or "").strip():
        raise HTTPException(400, detail=translate(lang, "iso.winpe_need_boot_wim"))

    try:
        from app.services.winpe_wim import boot_wim_filesystem_path

        boot_wim_filesystem_path(version)
    except FileNotFoundError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    try:
        from app.tasks.jobs import regenerate_winpe_scripts_task

        version.winpe_startnet_patched_at = None
        db.add(version)
        db.commit()
        regenerate_winpe_scripts_task.delay(version.id)
    except Exception as exc:
        raise HTTPException(500, detail=str(exc)) from exc

    return RedirectResponse(
        f"/isos/{version_id}?msg=winpe_scripts_queued",
        status_code=302,
    )


@router.get("/{version_id}/winpe-scripts/status")
async def winpe_scripts_status_route(
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """État génération scripts WinPE (polling UI après Celery)."""
    if _auth(request):
        raise HTTPException(401)
    lang = getattr(request.state, "locale", "fr")
    version = _get_version_view_or_404(db, request, version_id)
    _winpe_windows_version(version, lang)

    from app.datetime_display import format_local_dt
    from app.services.winpe_scripts import (
        DEPLOY_PS1,
        INJECT_DRIVERS_PS1,
        MASTERS_JSON,
        build_masters_catalog,
        scripts_dir,
    )

    patched = version.winpe_startnet_patched_at
    sdir = scripts_dir(version)
    scripts_ok = all((sdir / name).is_file() for name in (MASTERS_JSON, DEPLOY_PS1, INJECT_DRIVERS_PS1))
    installs = list(version.winpe_installs or [])
    masters = build_masters_catalog(version, installs)
    boot_wim_ok = True
    boot_wim_error = ""
    try:
        from app.services.winpe_wim import boot_wim_filesystem_path

        boot_wim_filesystem_path(version)
    except FileNotFoundError as exc:
        boot_wim_ok = False
        boot_wim_error = str(exc)
    ready = bool(
        patched and scripts_ok and len(masters) > 0 and boot_wim_ok
    )

    return JSONResponse(
        {
            "ready": ready,
            "pending": not ready and bool(installs),
            "patched_at": patched.isoformat() if patched else None,
            "patched_at_display": format_local_dt(patched) if patched else "",
            "masters_count": len(masters),
            "scripts_ok": scripts_ok,
            "scripts_dir": str(sdir),
            "boot_wim_ok": boot_wim_ok,
            "error": boot_wim_error,
        }
    )


@router.post("/{version_id}/winpe-installs/{install_id}/delete")
async def delete_winpe_install_route(
    version_id: int,
    install_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    version = _get_version_or_404(db, request, version_id)
    if not version:
        raise HTTPException(404)
    user = get_session_user(request)
    if not can_modify_iso_version(user, version):
        raise HTTPException(403)

    install = (
        db.query(WinpeInstall)
        .filter(
            WinpeInstall.id == install_id,
            WinpeInstall.iso_version_id == version.id,
        )
        .first()
    )
    if not install:
        raise HTTPException(404)

    from app.services.winpe_installs import delete_install_folder

    slug = install.slug
    if version.active_winpe_install_id == install.id:
        version.active_winpe_install_id = None
        version.winpe_startnet_patched_at = None
    db.delete(install)
    db.commit()
    delete_install_folder(version, slug)
    remaining = (
        db.query(WinpeInstall).filter(WinpeInstall.iso_version_id == version.id).count()
    )
    if remaining > 0 and version.boot_entry and version.boot_entry.boot_wim_path:
        try:
            from app.tasks.jobs import regenerate_winpe_scripts_task

            regenerate_winpe_scripts_task.delay(version.id)
        except Exception:
            pass
    return RedirectResponse(f"/isos/{version_id}", status_code=302)


@router.post("/{version_id}/delete")
async def delete_iso(version_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    version = _get_version_or_404(db, request, version_id)
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
        from app.services.iso_extractor import cleanup_boot_files

        cleanup_boot_files(os_slug, version.version_label, version_id)

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
            from app.services.menu_generator import queue_regenerate_all

            queue_regenerate_all()
        except Exception:
            pass

    except Exception as exc:
        import traceback
        traceback.print_exc()
        db.rollback()
        raise HTTPException(500, f"Erreur lors de la suppression : {exc}")

    return RedirectResponse("/isos", status_code=302)
