import shutil
from pathlib import Path
from app.config import settings


def get_disk_usage() -> dict:
    # Use configured path if it exists, otherwise fall back to current working dir
    path = Path(settings.http_root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(".")

    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {
            "total": 0, "used": 0, "free": 0, "percent": 0,
            "total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0,
        }

    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.used / usage.total * 100, 1),
        "total_gb": round(usage.total / 1024**3, 2),
        "used_gb": round(usage.used / 1024**3, 2),
        "free_gb": round(usage.free / 1024**3, 2),
    }


def get_dir_size(path: str | Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def fmt_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "—"
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        return "—"
    if size_bytes < 0:
        size_bytes = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
