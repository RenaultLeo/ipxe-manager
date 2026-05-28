from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings, sync_settings_runtime_from_db
from app.database import init_db, SessionLocal
from app.models.models import Upload, IsoVersion
from app.routers import auth, dashboard, isos, boot_files, configs, menus, jobs, firmware, locale, admin, admin_supervision
from app.routers import settings as settings_router

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR.parent / "static"

# ── Ensure all required directories exist at import time ─────────────────────
for _d in [
    settings.tftp_root,
    settings.http_root,
    settings.iso_root,
    settings.build_dir,
    str(settings.menus_dir),
    str(settings.boot_dir),
    str(settings.configs_dir),
    str(settings.ipxe_src_dir.parent),  # /srv/ipxe/build
]:
    try:
        Path(_d).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # On a dev machine the paths may be read-only — that's fine

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="iPXE Manager", version="1.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400,
    same_site="lax",
    https_only=False,
)

# ── Static assets (CSS/JS) ────────────────────────────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── iPXE file serving ────────────────────────────────────────────────────────
# In production these paths are served directly by Nginx (see deploy/nginx.conf).
# In dev mode FastAPI serves them under /ipxe-files/ to avoid router conflicts.
_http = Path(settings.http_root)
for _path, _subdir, _name in [
    ("/ipxe-files/boot",    "boot",    "ipxe_boot"),
    ("/ipxe-files/menus",   "menus",   "ipxe_menus"),
    ("/ipxe-files/configs", "configs", "ipxe_configs"),
]:
    _dir = _http / _subdir
    try:
        _dir.mkdir(parents=True, exist_ok=True)
        app.mount(_path, StaticFiles(directory=str(_dir)), name=_name)
    except (OSError, RuntimeError):
        pass

# ── Routers ───────────────────────────────────────────────────────────────────
@app.middleware("http")
async def locale_middleware(request: Request, call_next):
    from app.i18n import LOCALE_COOKIE, resolve_lang

    raw = request.cookies.get(LOCALE_COOKIE, "fr")
    request.state.locale = resolve_lang(raw)
    return await call_next(request)


app.include_router(locale.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(isos.router)
app.include_router(boot_files.router)
app.include_router(configs.router)
app.include_router(menus.router)
app.include_router(jobs.router)
app.include_router(firmware.router)
app.include_router(settings_router.router)
app.include_router(admin.router)
app.include_router(admin_supervision.router)


def _cleanup_stale_uploads():
    """Au démarrage, marque comme 'error' les uploads restés bloqués en pending/processing.
    Ces zombies apparaissent quand le worker Celery est redémarré pendant une tâche."""
    try:
        db = SessionLocal()
        stale = db.query(Upload).filter(Upload.status.in_(["pending", "processing"])).all()
        for u in stale:
            u.status = "error"
            u.error_msg = "Interrompu (redémarrage du serveur)"
        # Pareil pour les versions restées en extracting
        stuck = db.query(IsoVersion).filter(IsoVersion.status == "extracting").all()
        for v in stuck:
            v.status = "error"
        if stale or stuck:
            db.commit()
        db.close()
    except Exception:
        pass


@app.on_event("startup")
async def startup():
    import logging

    log = logging.getLogger(__name__)
    try:
        init_db()
        sync_settings_runtime_from_db()
        _cleanup_stale_uploads()
    except Exception:
        log.exception("Échec au démarrage (init_db / settings / cleanup)")
        raise
