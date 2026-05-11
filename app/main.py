from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db
from app.routers import auth, dashboard, isos, boot_files, configs, menus
from app.routers import settings as settings_router

BASE_DIR = Path(__file__).parent

app = FastAPI(title="iPXE Manager", version="1.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400,
    same_site="lax",
    https_only=False,
)

# ── Static assets (CSS, JS) ───────────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR.parent / "static")),
    name="static",
)

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
    # Create required directories
    for d in [
        settings.tftp_root,
        settings.http_root,
        settings.iso_root,
        str(settings.menus_dir),
        str(settings.boot_dir),
        str(settings.configs_dir),
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # Initialize database tables
    init_db()

    # Mount iPXE file-serving endpoints AFTER dirs exist
    # (avoids StaticFiles crashing on missing dir at import time)
    http_root = Path(settings.http_root)
    existing_paths = {r.path for r in app.routes if hasattr(r, "path")}

    mounts = [
        ("/boot",    str(http_root / "boot"),    "ipxe_boot"),
        ("/menus",   str(http_root / "menus"),   "ipxe_menus"),
        ("/configs", str(http_root / "configs"), "ipxe_configs"),
    ]
    for path, directory, name in mounts:
        if path not in existing_paths:
            app.mount(path, StaticFiles(directory=directory), name=name)
