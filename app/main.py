from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings, sync_settings_runtime_from_db
from app.database import init_db, SessionLocal
from app.models.models import Upload, IsoVersion
from app.routers import auth, dashboard, isos, boot_files, configs, menus, jobs, firmware, locale, admin, admin_supervision
from app.routers import settings as settings_router
from app.services.network_traffic_store import start_network_traffic_collector

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    log = logging.getLogger(__name__)
    try:
        if settings.secret_key == "changeme_generate_with_openssl_rand_hex_32":
            raise RuntimeError(
                "SECRET_KEY is insecure default. Set SECRET_KEY in .env before starting."
            )
        init_db()
        sync_settings_runtime_from_db()
        _cleanup_stale_uploads()
        start_network_traffic_collector(sample_interval_sec=10.0)
    except Exception:
        log.exception("Échec au démarrage (init_db / settings / cleanup)")
        raise
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="iPXE Manager", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400,
    same_site="lax",
    https_only=settings.server_base_url.strip().lower().startswith("https://"),
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
async def multipart_limits_middleware(request: Request, call_next):
    """Erreurs multipart remontées par les handlers (pas de preload : conflit Form/File FastAPI)."""
    from starlette.responses import JSONResponse

    from app.http_multipart import _multipart_http_error
    from starlette.formparsers import MultiPartException

    try:
        return await call_next(request)
    except MultiPartException as exc:
        lang = getattr(request.state, "locale", "fr")
        http_exc = _multipart_http_error(exc, lang=lang)
        return JSONResponse({"detail": http_exc.detail}, status_code=http_exc.status_code)


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


