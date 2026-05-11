from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db
from app.routers import auth, dashboard, isos, boot_files, configs, menus
from app.routers import settings as settings_router

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR.parent / "static"

# ── Ensure all required directories exist at import time ─────────────────────
for _d in [
    settings.tftp_root,
    settings.http_root,
    settings.iso_root,
    str(settings.menus_dir),
    str(settings.boot_dir),
    str(settings.configs_dir),
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
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(isos.router)
app.include_router(boot_files.router)
app.include_router(configs.router)
app.include_router(menus.router)
app.include_router(settings_router.router)


@app.on_event("startup")
async def startup():
    init_db()
