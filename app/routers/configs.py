import shutil
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from urllib.parse import quote

from app.database import get_db
from app.auth import is_authenticated
from app.models.models import IsoVersion, AutoConfig
from app.services.config_scanner import OS_CONFIG_TYPE, FORCED_CONFIGS
from app.services.slugify import slugify
from app.templating import templates, template_context
from app.config import settings
from app.i18n import translate

router = APIRouter(prefix="/ipxe-configs")

CONFIG_TYPES = [
    "preseed",
    "kickstart",
    "unattend",
    "cloud-init",
    "proxmox-answer",
    "alpine-answer",
    "custom",
]

UBUNTU_CLOUD_PREFIX = "conf-cloudInit-"


def _config_type_labels(lang: str) -> dict[str, str]:
    """Libellés lisibles du select « type » (traduits)."""
    out: dict[str, str] = {}
    for ct in CONFIG_TYPES:
        key = "cfg.type_dd_" + ct.replace("-", "_")
        out[ct] = translate(lang, key)
    return out


def _auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


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
    configs = db.query(AutoConfig).order_by(AutoConfig.updated_at.desc()).all()
    versions = db.query(IsoVersion).all()
    return templates.TemplateResponse(
        "configs/index.html",
        template_context(
            request,
            configs=configs,
            versions=versions,
            config_types=CONFIG_TYPES,
            scan_result=scan_result,
        ),
    )


@router.post("/scan")
async def config_scan(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
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
async def config_new(request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    versions = db.query(IsoVersion).all()
    lang = getattr(request.state, "locale", "fr")
    return templates.TemplateResponse(
        "configs/edit.html",
        template_context(
            request,
            config=None,
            versions=versions,
            config_types=CONFIG_TYPES,
            server_url=settings.server_base_url,
            os_config_type=OS_CONFIG_TYPE,
            forced_configs=FORCED_CONFIGS,
            config_type_labels=_config_type_labels(lang),
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

    version = db.query(IsoVersion).get(iso_version_id)
    if not version:
        raise HTTPException(404)

    os_slug = version.os_type.slug
    if version.os_type.is_builtin and os_slug in FORCED_CONFIGS:
        if config_type not in CONFIG_TYPES:
            config_type = FORCED_CONFIGS[os_slug]["type"]

    cfg = AutoConfig(
        iso_version_id=iso_version_id,
        config_type=config_type,
        label=label or "user-data",
        content=content,
    )

    if (
        os_slug == "ubuntu"
        and version.os_type.is_builtin
        and config_type == "cloud-init"
    ):
        slug = slugify(cloud_bundle_name.strip())
        if not slug:
            raise HTTPException(
                400,
                "Pour Ubuntu, indiquez un nom de dossier de configuration (slug non vide).",
            )
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
        return RedirectResponse("/ipxe-configs", status_code=302)

    db.add(cfg)
    db.flush()
    file_path = _write_config_file(cfg, version, content, forced_filename=forced_filename)
    cfg.file_path = file_path
    db.commit()
    return RedirectResponse("/ipxe-configs", status_code=302)


@router.get("/{config_id}/edit", response_class=HTMLResponse)
async def config_edit(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)
    versions = db.query(IsoVersion).all()
    lang = getattr(request.state, "locale", "fr")
    return templates.TemplateResponse(
        "configs/edit.html",
        template_context(
            request,
            config=cfg,
            versions=versions,
            config_types=CONFIG_TYPES,
            server_url=settings.server_base_url,
            os_config_type=OS_CONFIG_TYPE,
            forced_configs=FORCED_CONFIGS,
            config_type_labels=_config_type_labels(lang),
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
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)

    cfg.label = label or cfg.label or "user-data"
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
        return RedirectResponse("/ipxe-configs", status_code=302)

    fp = _write_config_file(cfg, ver, content)
    cfg.file_path = fp
    db.commit()
    return RedirectResponse("/ipxe-configs", status_code=302)


@router.post("/{config_id}/delete")
async def config_delete(config_id: int, request: Request, db: Session = Depends(get_db)):
    redir = _auth(request)
    if redir:
        return redir
    cfg = db.query(AutoConfig).get(config_id)
    if not cfg:
        raise HTTPException(404)

    if cfg.ubuntu_cloud_slug and cfg.file_path:
        _delete_ubuntu_bundle_disk(cfg)
    elif cfg.file_path:
        Path(settings.http_root).joinpath(cfg.file_path).unlink(missing_ok=True)

    db.delete(cfg)
    db.commit()
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
    return f"configs/{version.os_type.slug}/{version_slug}/{fname}"
