import logging
import shutil
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from urllib.parse import quote

from app.database import get_db
from app.auth import auth_redirect_admin, auth_redirect_login, get_session_user
from app.services.ownership import (
    can_modify_iso_version,
    filter_iso_versions,
    get_autoconfig,
    get_autoconfig_view,
    get_iso_version,
    get_iso_version_view,
)
from app.models.models import IsoVersion, AutoConfig
from app.services.config_scanner import OS_CONFIG_TYPE, FORCED_CONFIGS
from app.services.slugify import slugify
from app.templating import templates, template_context
from app.config import settings, resolve_server_base_url
from app.i18n import translate

from app.services.autoconfig_types import (
    CONFIG_TYPES,
    all_config_types_for_ui,
    config_type_labels as _config_type_labels,
)
from app.services.autoconfig_label import (
    label_from_ubuntu_cloud_slug,
    next_ubuntu_cloud_slug,
    normalize_new_config_label,
    resolve_autoconfig_menu_label,
)

router = APIRouter(prefix="/ipxe-configs")

UBUNTU_CLOUD_PREFIX = "conf-cloudInit-"


def _queue_proxmox_after_config_push(
    db: Session, version: IsoVersion, cfg: AutoConfig
):
    """Lance l’injection answer.toml dans proxmox-netboot-autoinstall.iso (tâche Celery)."""
    if (version.os_type.slug or "").lower() != "proxmox":
        return None
    if cfg.config_type != "proxmox-answer":
        return None
    from app.services.proxmox_autoinstall import queue_proxmox_inject

    return queue_proxmox_inject(db, version, cfg)


def _maybe_republish_active_ubuntu(db: Session, version: IsoVersion, cfg: AutoConfig) -> None:
    """Si cette config est la config courante, recopie vers boot/ et régénère les menus."""
    if getattr(version, "active_autoconfig_id", None) != cfg.id:
        return
    try:
        from app.services.autoconfig_publish import publish_ubuntu_cloud_config
        from app.services.menu_generator import queue_regenerate_all

        publish_ubuntu_cloud_config(version, cfg)
        queue_regenerate_all()
    except Exception:
        logger.exception("Republication config courante Ubuntu (version %s)", version.id)


def _auth(request: Request):
    return auth_redirect_login(request)


def _ubuntu_bundle_rel_path(os_slug: str, version_slug: str, cloud_slug: str) -> str:
    return f"configs/{os_slug}/{version_slug}/{UBUNTU_CLOUD_PREFIX}{cloud_slug}"


def _write_ubuntu_cloud_bundle(
    version: IsoVersion,
    cloud_slug: str,
    user_data: str,
    meta_data: str,
) -> str:
    """Écrit user-data + meta-data dans conf-cloudInit-<slug>/. Retourne le chemin relatif du dossier."""
    version_slug = slugify(version.version_label)
    base = settings.configs_dir / version.os_type.slug / version_slug
    bundle_dir = base / f"{UBUNTU_CLOUD_PREFIX}{cloud_slug}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "user-data").write_text(user_data, encoding="utf-8")
    (bundle_dir / "meta-data").write_text(meta_data, encoding="utf-8")
    return _ubuntu_bundle_rel_path(version.os_type.slug, version_slug, cloud_slug)


def _delete_ubuntu_bundle_disk(cfg: AutoConfig):
    if not cfg.ubuntu_cloud_slug or not cfg.file_path:
        return
    root = Path(settings.http_root)
    bundle = root / cfg.file_path.lstrip("/")
    if bundle.is_dir() and bundle.name.startswith(UBUNTU_CLOUD_PREFIX):
        shutil.rmtree(bundle, ignore_errors=True)


@router.get("", response_class=HTMLResponse)
async def config_list(request: Request, db: Session = Depends(get_db),
                      scan_result: str = ""):
    redir = _auth(request)
    if redir:
        return redir
    configs = (
        db.query(AutoConfig)
        .options(
            joinedload(AutoConfig.iso_version).joinedload(IsoVersion.os_type),
        )
        .order_by(AutoConfig.updated_at.desc())
        .all()
    )
    config_menu_labels = {
        c.id: resolve_autoconfig_menu_label(c) for c in configs
    }
    versions = db.query(IsoVersion).order_by(IsoVersion.version_label).all()
    types_combo = all_config_types_for_ui(db)
    return templates.TemplateResponse(
        "configs/index.html",
        template_context(
            request,
            configs=configs,
            config_menu_labels=config_menu_labels,
            versions=versions,
            config_types=types_combo,
            scan_result=scan_result,
        ),
    )


@router.post("/scan")
async def config_scan(request: Request, db: Session = Depends(get_db)):
    redir = auth_redirect_admin(request)
    if redir:
        return redir
    from app.services.config_scanner import scan_and_import
    res = scan_and_import(db)
    lang = getattr(request.state, "locale", "fr")
    msg = translate(
        lang,
        "cfg.scan_done",
        imported=res["imported"],
        skipped=res["skipped"],
    )
    if res.get("errors"):
        msg += translate(lang, "cfg.scan_errors_suffix", n=len(res["errors"]))
    return RedirectResponse(f"/ipxe-configs?scan_result={quote(msg)}", status_code=302)


@router.get("/new", response_class=HTMLResponse)
async def config_new(
    request: Request,
    db: Session = Depends(get_db),
    preset_version: int | None = Query(None, gt=0),
):
    redir = _auth(request)
    if redir:
        return redir
    user = get_session_user(request)
    versions = filter_iso_versions(db, user).all()
    lang = getattr(request.state, "locale", "fr")
    types_combo = all_config_types_for_ui(db)
    return templates.TemplateResponse(
        "configs/edit.html",
        template_context(
            request,
            config=None,
            menu_display_label="",
            versions=versions,
            preset_iso_version_id=preset_version,
            config_types=types_combo,
            server_url=resolve_server_base_url(db),
            os_config_type=OS_CONFIG_TYPE,
            forced_configs=FORCED_CONFIGS,
            config_type_labels=_config_type_labels(lang, types_combo),
        ),
    )


@router.post("/new")
async def config_create(
    request: Request,
    iso_version_id: int = Form(...),
    config_type: str = Form(...),
    forced_filename: str = Form(""),
    cloud_bundle_name: str = Form(""),
    label: str = Form(""),
    content: str = Form(""),
    content_meta: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir

    user = get_session_user(request)
    version = get_iso_version(db, user, iso_version_id)
    if not version:
        raise HTTPException(404)

    os_slug = version.os_type.slug
    allowed_types = set(all_config_types_for_ui(db))

    if version.os_type.is_builtin and os_slug in FORCED_CONFIGS:
        cfg_ct = FORCED_CONFIGS[os_slug]["type"]
        if config_type not in CONFIG_TYPES:
            config_type = cfg_ct
        if config_type != cfg_ct:
            config_type = cfg_ct
    else:
        fc = (version.os_type.forced_autoconfig_type or "").strip()
        if fc:
            config_type = fc
        elif config_type not in allowed_types:
            raise HTTPException(400, "Type de configuration non autorisé.")

    menu_label = normalize_new_config_label(db, iso_version_id, label)

    cfg = AutoConfig(
        iso_version_id=iso_version_id,
        config_type=config_type,
        label=menu_label,
        content=content,
    )

    if (
        os_slug == "ubuntu"
        and version.os_type.is_builtin
        and config_type == "cloud-init"
    ):
        raw_slug = cloud_bundle_name.strip()
        if raw_slug:
            slug = slugify(raw_slug)
            if not slug:
                raise HTTPException(
                    400,
                    "Nom de dossier invalide (utilisez lettres, chiffres, tirets).",
                )
        else:
            slug = next_ubuntu_cloud_slug(db, iso_version_id)
            cfg.label = label_from_ubuntu_cloud_slug(slug)
        dup = (
            db.query(AutoConfig)
            .filter(
                AutoConfig.iso_version_id == version.id,
                AutoConfig.config_type == "cloud-init",
                AutoConfig.ubuntu_cloud_slug == slug,
            )
            .first()
        )
        if dup:
            raise HTTPException(400, f"Une config « {UBUNTU_CLOUD_PREFIX}{slug} » existe déjà pour cette version.")
        cfg.meta_data_content = content_meta
        cfg.ubuntu_cloud_slug = slug
        db.add(cfg)
        db.flush()
        fp = _write_ubuntu_cloud_bundle(version, slug, content, content_meta or "")
        cfg.file_path = fp
        db.commit()
        _maybe_republish_active_ubuntu(db, version, cfg)
        return RedirectResponse("/ipxe-configs", status_code=302)

    db.add(cfg)
    db.flush()
    file_path = _write_config_file(cfg, version, content, forced_filename=forced_filename)
    cfg.file_path = file_path
    db.commit()
    upload = _queue_proxmox_after_config_push(db, version, cfg)
    if upload:
        return RedirectResponse(
            f"/isos/{version.id}?msg=proxmox_inject_started&upload_id={upload.id}",
            status_code=302,
        )
    return RedirectResponse("/ipxe-configs", status_code=302)


@router.get("/{config_id}/edit", response_class=HTMLResponse)
async def config_edit(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    user = get_session_user(request)
    cfg = get_autoconfig_view(db, user, config_id)
    if not cfg:
        raise HTTPException(404)
    ver = get_iso_version_view(db, user, cfg.iso_version_id)
    can_modify = can_modify_iso_version(user, ver) if ver else False
    if can_modify:
        versions = filter_iso_versions(db, user).all()
    else:
        versions = [ver] if ver else []
    lang = getattr(request.state, "locale", "fr")
    types_combo = all_config_types_for_ui(db)
    return templates.TemplateResponse(
        "configs/edit.html",
        template_context(
            request,
            config=cfg,
            menu_display_label=resolve_autoconfig_menu_label(cfg),
            versions=versions,
            preset_iso_version_id=None,
            config_types=types_combo,
            server_url=resolve_server_base_url(db),
            os_config_type=OS_CONFIG_TYPE,
            forced_configs=FORCED_CONFIGS,
            config_type_labels=_config_type_labels(lang, types_combo),
            can_modify=can_modify,
        ),
    )


@router.post("/{config_id}/edit")
async def config_update(
    config_id: int,
    request: Request,
    label: str = Form(""),
    content: str = Form(""),
    content_meta: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _auth(request)
    if redir:
        return redir
    user = get_session_user(request)
    cfg = get_autoconfig(db, user, config_id)
    if not cfg:
        raise HTTPException(404)

    cfg.label = normalize_new_config_label(
        db,
        cfg.iso_version_id,
        label,
        exclude_id=cfg.id,
        ubuntu_cloud_slug=cfg.ubuntu_cloud_slug,
    )
    cfg.content = content
    cfg.updated_at = datetime.utcnow()

    ver = cfg.iso_version
    if (
        cfg.config_type == "cloud-init"
        and ver.os_type.slug == "ubuntu"
        and cfg.ubuntu_cloud_slug
    ):
        cfg.meta_data_content = content_meta
        fp = _write_ubuntu_cloud_bundle(
            ver, cfg.ubuntu_cloud_slug, content, content_meta or ""
        )
        cfg.file_path = fp
        db.commit()
        _maybe_republish_active_ubuntu(db, ver, cfg)
        return RedirectResponse("/ipxe-configs", status_code=302)

    fp = _write_config_file(cfg, ver, content)
    cfg.file_path = fp
    db.commit()
    upload = _queue_proxmox_after_config_push(db, ver, cfg)
    if upload:
        return RedirectResponse(
            f"/isos/{ver.id}?msg=proxmox_inject_started&upload_id={upload.id}",
            status_code=302,
        )
    return RedirectResponse("/ipxe-configs", status_code=302)


@router.post("/{config_id}/delete")
async def config_delete(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    user = get_session_user(request)
    cfg = get_autoconfig(db, user, config_id)
    if not cfg:
        raise HTTPException(404)

    ver = cfg.iso_version
    was_active = ver and getattr(ver, "active_autoconfig_id", None) == cfg.id

    if cfg.ubuntu_cloud_slug and cfg.file_path:
        _delete_ubuntu_bundle_disk(cfg)
    elif cfg.file_path:
        Path(settings.http_root).joinpath(cfg.file_path).unlink(missing_ok=True)

    if was_active and ver:
        ver.active_autoconfig_id = None
        db.add(ver)
    db.delete(cfg)
    db.commit()
    if was_active and ver:
        from app.services.menu_generator import queue_regenerate_all

        if (ver.os_type.slug or "").lower() == "ubuntu":
            from app.services.autoconfig_publish import (
                boot_version_dir,
                clear_ubuntu_seed_from_boot,
            )

            clear_ubuntu_seed_from_boot(boot_version_dir(ver))
        elif (ver.os_type.slug or "").lower() == "proxmox":
            from app.services.autoconfig_publish import clear_proxmox_answer_from_boot

            clear_proxmox_answer_from_boot(ver)
        queue_regenerate_all()
    return RedirectResponse("/ipxe-configs", status_code=302)


def _write_config_file(cfg: AutoConfig, version: IsoVersion, content: str,
                       forced_filename: str = "") -> str:
    """Write config content to disk and return the relative path."""
    version_slug = slugify(version.version_label)
    cfg_dir = settings.configs_dir / version.os_type.slug / version_slug
    cfg_dir.mkdir(parents=True, exist_ok=True)

    ext_map = {
        "preseed":          "cfg",
        "kickstart":        "cfg",
        "esxi-kickstart":   "cfg",
        "unattend":         "xml",
        "cloud-init":       "",
        "proxmox-answer":   "toml",
        "alpine-answer":    "",
        "custom":           "txt",
    }

    os_slug = version.os_type.slug
    forced = FORCED_CONFIGS.get(os_slug) if version.os_type.is_builtin else None

    if forced:
        if forced.get("multi_file") and forced.get("filenames") and forced_filename in forced["filenames"]:
            fname = forced_filename
        elif forced.get("filenames"):
            fname = forced["filenames"][0]
        else:
            base = slugify(cfg.label) if cfg.label and slugify(cfg.label) else cfg.config_type
            ext = ext_map.get(cfg.config_type, "txt")
            fname = f"{base}.{ext}" if ext else base
    else:
        ext = ext_map.get(cfg.config_type, "txt")
        base = slugify(cfg.label) if cfg.label and slugify(cfg.label) else cfg.config_type
        fname = f"{base}.{ext}" if ext else base

        dest = cfg_dir / fname
        if dest.exists():
            existing_rel = f"configs/{version.os_type.slug}/{version_slug}/{fname}"
            from app.database import SessionLocal
            db_check = SessionLocal()
            conflict = db_check.query(AutoConfig).filter(
                AutoConfig.file_path == existing_rel,
                AutoConfig.id != cfg.id,
            ).first()
            db_check.close()
            if conflict:
                base_name = Path(fname).stem
                suffix = Path(fname).suffix
                fname = f"{base_name}_{cfg.id}{suffix}"
                dest = cfg_dir / fname

    dest = cfg_dir / fname
    dest.write_text(content, encoding="utf-8")
    rel = f"configs/{version.os_type.slug}/{version_slug}/{fname}"
    if (
        (version.os_type.slug or "").lower() == "proxmox"
        and cfg.config_type == "proxmox-answer"
    ):
        try:
            from app.services.autoconfig_publish import publish_proxmox_answer_config

            publish_proxmox_answer_config(version, content)
        except Exception:
            logger.exception(
                "Copie answer.toml vers boot/proxmox/<version>/ (version %s)",
                version.id,
            )
    return rel
