"""Collecte d'état serveur pour la page admin Supervision (lecture seule)."""
from __future__ import annotations

import logging
import os
import platform
import socket
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil
from sqlalchemy.orm import Session

from app.config import settings
from app.services.disk_info import get_disk_usage, get_dir_size, fmt_size

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SYSTEMD_UNITS = (
    "nginx",
    "redis-server",
    "tftpd-hpa",
    "ipxe-manager",
    "ipxe-celery",
)

EXTENDED_SYSTEMD_UNITS = DEFAULT_SYSTEMD_UNITS + ("smbd", "nmbd", "nfs-server", "nfs-kernel-server")

MONITORED_PORTS: tuple[tuple[str, int, str], ...] = (
    ("tcp", 80, "HTTP (Nginx)"),
    ("tcp", 443, "HTTPS"),
    ("tcp", 8000, "Uvicorn (app)"),
    ("tcp", 6379, "Redis"),
    ("tcp", 445, "SMB"),
    ("udp", 69, "TFTP"),
    ("tcp", 2049, "NFS"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_from_bool(ok: bool | None, warn_if_false: bool = False) -> str:
    if ok is None:
        return "unknown"
    if ok:
        return "ok"
    return "warn" if warn_if_false else "error"


def _run_cmd(
    cmd: list[str],
    *,
    timeout: float = 12.0,
    cwd: str | Path | None = None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 125, "", str(exc)


def _systemctl_argv(*args: str) -> list[list[str]]:
    """Essaie sudo -n puis systemctl direct."""
    base = list(args)
    return [
        ["sudo", "-n", "systemctl", *base],
        ["systemctl", *base],
    ]


def systemctl_is_active(unit: str) -> dict[str, Any]:
    state = "unknown"
    detail = ""
    used_sudo = False
    for cmd in _systemctl_argv("is-active", unit):
        code, out, err = _run_cmd(cmd, timeout=8)
        if code == 127:
            return {
                "unit": unit,
                "active": None,
                "state": "unavailable",
                "detail": "systemctl introuvable",
                "sudo": False,
            }
        if code in (0, 3, 4):
            used_sudo = cmd[0] == "sudo"
            if code == 0:
                state = "active"
            elif out in ("inactive", "failed", "activating", "deactivating"):
                state = out
            else:
                state = out or "inactive"
            detail = err or out
            break
        detail = err or out or f"exit {code}"
    else:
        state = "error"
    return {
        "unit": unit,
        "active": state == "active",
        "state": state,
        "detail": detail[:200],
        "sudo": used_sudo,
    }


def systemctl_restart(unit: str) -> dict[str, Any]:
    last_err = ""
    for cmd in _systemctl_argv("restart", unit):
        code, out, err = _run_cmd(cmd, timeout=90)
        if code == 127:
            return {"unit": unit, "ok": False, "detail": "systemctl introuvable", "sudo": False}
        if code == 0:
            return {"unit": unit, "ok": True, "detail": (out or "OK")[:200], "sudo": cmd[0] == "sudo"}
        last_err = err or out or f"exit {code}"
    return {"unit": unit, "ok": False, "detail": last_err[:300], "sudo": False}


def check_port_open(proto: str, port: int, host: str = "127.0.0.1") -> bool | None:
    try:
        if proto == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                return False
            finally:
                s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            return s.connect_ex((host, port)) == 0
        finally:
            s.close()
    except OSError:
        return None


def check_redis() -> dict[str, Any]:
    for cmd in (["redis-cli", "ping"], ["sudo", "-n", "redis-cli", "ping"]):
        code, out, err = _run_cmd(cmd, timeout=5)
        if code == 127:
            continue
        txt = (out or err).lower()
        ok = code == 0 and "pong" in txt
        return {"ok": ok, "detail": (out or err)[:120], "status": _status_from_bool(ok)}
    return {"ok": None, "detail": "redis-cli introuvable", "status": "unknown"}


def check_celery(app_dir: Path | None = None, venv: Path | None = None) -> dict[str, Any]:
    app_dir = app_dir or PROJECT_ROOT
    venv = venv or _detect_venv()
    celery_bin = venv / "bin" / "celery"
    if not celery_bin.is_file():
        return {"ok": None, "detail": f"Celery absent : {celery_bin}", "status": "unknown"}
    code, out, err = _run_cmd(
        [str(celery_bin), "-A", "app.tasks.celery_app", "inspect", "ping"],
        timeout=25,
        cwd=app_dir,
    )
    txt = (out or "") + (err or "")
    ok = code == 0 and "pong" in txt.lower()
    return {
        "ok": ok,
        "detail": (txt.strip().splitlines()[:3] or [f"exit {code}"])[:3],
        "status": _status_from_bool(ok),
    }


def _detect_venv() -> Path:
    env = os.environ.get("IPXE_VENV", "").strip()
    if env:
        return Path(env)
    candidate = PROJECT_ROOT.parent / "venv"
    if candidate.is_dir():
        return candidate
    return Path("/srv/ipxe/venv")


def check_database() -> dict[str, Any]:
    url = settings.database_url
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1] if "///" in url else url.replace("sqlite:///", "")
        p = Path(path)
        if not p.is_file():
            return {"ok": False, "engine": "sqlite", "detail": f"fichier absent : {p}", "status": "error"}
        try:
            con = sqlite3.connect(str(p), timeout=5)
            try:
                row = con.execute("PRAGMA integrity_check").fetchone()
                ok = row and row[0] == "ok"
                tables = {
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                return {
                    "ok": ok,
                    "engine": "sqlite",
                    "detail": row[0] if row else "?",
                    "tables": len(tables),
                    "status": _status_from_bool(ok),
                }
            finally:
                con.close()
        except sqlite3.Error as exc:
            return {"ok": False, "engine": "sqlite", "detail": str(exc)[:200], "status": "error"}
    code, out, err = _run_cmd(["pg_isready"], timeout=5)
    ok = code == 0
    return {
        "ok": ok,
        "engine": "postgresql",
        "detail": (out or err)[:200],
        "status": _status_from_bool(ok),
    }


def check_paths() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for label, raw in (
        ("TFTP", settings.tftp_root),
        ("HTTP", settings.http_root),
        ("ISO", settings.iso_root),
        ("Build", settings.build_dir),
        ("Menus", str(settings.menus_dir)),
        ("Boot", str(settings.boot_dir)),
        ("Configs", str(settings.configs_dir)),
    ):
        p = Path(raw)
        exists = p.exists()
        writable = False
        if exists:
            try:
                test = p / ".write_probe"
                test.touch()
                test.unlink(missing_ok=True)
                writable = True
            except OSError:
                writable = False
        checks.append(
            {
                "label": label,
                "path": str(p),
                "exists": exists,
                "writable": writable,
                "status": _status_from_bool(exists and writable, warn_if_false=exists and not writable),
            }
        )
    menu_ipxe = settings.menus_dir / "menu.ipxe"
    checks.append(
        {
            "label": "menu.ipxe",
            "path": str(menu_ipxe),
            "exists": menu_ipxe.is_file(),
            "writable": False,
            "status": _status_from_bool(menu_ipxe.is_file(), warn_if_false=True),
        }
    )
    return checks


def host_metrics() -> dict[str, Any]:
    try:
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.3)
        boot = psutil.boot_time()
        uptime_s = int(time.time() - boot)
    except Exception as exc:
        return {"error": str(exc)[:200]}
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "mount": part.mountpoint,
                    "percent": round(u.percent, 1),
                    "used_gb": round(u.used / 1024**3, 2),
                    "total_gb": round(u.total / 1024**3, 2),
                }
            )
        except (PermissionError, OSError):
            continue
    net: list[dict[str, Any]] = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_io_counters(pernic=True)
        for nic, st in stats.items():
            if nic == "lo":
                continue
            ips = [
                a.address
                for a in addrs.get(nic, [])
                if getattr(a, "family", None) == socket.AF_INET
            ]
            net.append(
                {
                    "iface": nic,
                    "ips": ips[:3],
                    "rx_mb": round(st.bytes_recv / 1024**2, 1),
                    "tx_mb": round(st.bytes_sent / 1024**2, 1),
                }
            )
    except Exception:
        pass
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "cpu_percent": cpu,
        "memory_percent": round(vm.percent, 1),
        "memory_used_gb": round(vm.used / 1024**3, 2),
        "memory_total_gb": round(vm.total / 1024**3, 2),
        "uptime_seconds": uptime_s,
        "uptime_human": _fmt_uptime(uptime_s),
        "disk_partitions": disks[:6],
        "network": net[:8],
    }


def _fmt_uptime(seconds: int) -> str:
    d, rem = divmod(max(0, seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}j")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}min")
    return " ".join(parts)


def application_stats(db: Session) -> dict[str, Any]:
    from app.models.models import IsoVersion, Upload, User

    users = db.query(User).count()
    isos = db.query(IsoVersion).count()
    uploads_pending = db.query(Upload).filter(Upload.status.in_(["pending", "processing"])).count()
    extracting = db.query(IsoVersion).filter(IsoVersion.status == "extracting").count()
    http_disk = get_disk_usage()
    iso_bytes = get_dir_size(settings.iso_root)
    http_bytes = get_dir_size(settings.http_root)
    base = settings.server_base_url.rstrip("/")
    return {
        "users": users,
        "iso_versions": isos,
        "uploads_in_progress": uploads_pending,
        "iso_extracting": extracting,
        "server_base_url": base,
        "http_disk": http_disk,
        "iso_dir_size": fmt_size(iso_bytes),
        "http_dir_size": fmt_size(http_bytes),
    }


def collect_snapshot(db: Session, *, extended_units: bool = True) -> dict[str, Any]:
    units = EXTENDED_SYSTEMD_UNITS if extended_units else DEFAULT_SYSTEMD_UNITS
    seen: set[str] = set()
    services: list[dict[str, Any]] = []
    for unit in units:
        if unit in seen:
            continue
        seen.add(unit)
        info = systemctl_is_active(unit)
        if info["state"] == "unavailable" and unit.startswith("nfs-kernel"):
            continue
        services.append(info)

    ports: list[dict[str, Any]] = []
    for proto, port, label in MONITORED_PORTS:
        open_ = check_port_open(proto, port)
        ports.append(
            {
                "proto": proto,
                "port": port,
                "label": label,
                "open": open_,
                "status": _status_from_bool(open_, warn_if_false=True),
            }
        )

    venv = _detect_venv()
    paths = check_paths()
    checks = [
        {"id": "redis", "label": "Redis", **check_redis()},
        {"id": "celery", "label": "Celery", **check_celery(PROJECT_ROOT, venv)},
        {"id": "database", "label": "Base de données", **check_database()},
    ]

    svc_active = sum(1 for s in services if s.get("active"))
    svc_total = len(services)
    port_open = sum(1 for p in ports if p.get("open"))

    return {
        "generated_at": _utc_now(),
        "host": host_metrics(),
        "application": application_stats(db),
        "services": services,
        "services_summary": {"active": svc_active, "total": svc_total, "inactive": svc_total - svc_active},
        "ports": ports,
        "ports_summary": {"open": port_open, "total": len(ports)},
        "paths": paths,
        "checks": checks,
        "paths_ok": sum(1 for p in paths if p["status"] == "ok"),
        "paths_total": len(paths),
        "can_sudo_systemctl": _probe_sudo_systemctl(),
        "project_root": str(PROJECT_ROOT),
        "venv": str(venv),
    }


def _probe_sudo_systemctl() -> bool:
    code, _, _ = _run_cmd(["sudo", "-n", "systemctl", "is-active", "nginx"], timeout=5)
    return code == 0


def http_probe(base_url: str, path: str = "/login", timeout: float = 8.0) -> dict[str, Any]:
    import http.client
    from urllib.parse import urlparse

    p = urlparse(base_url)
    if not p.scheme or not p.hostname:
        return {"ok": False, "status": -1, "detail": "URL invalide"}
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        if p.scheme == "https":
            import ssl

            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(p.hostname, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(p.hostname, port, timeout=timeout)
        conn.request("GET", path, headers={"User-Agent": "ipxe-supervision/1.0"})
        resp = conn.getresponse()
        resp.read()
        code = resp.status
        conn.close()
        ok = code in (200, 301, 302, 303, 307, 308)
        return {"ok": ok, "status": code, "detail": f"HTTP {code}"}
    except Exception as exc:
        return {"ok": False, "status": -1, "detail": str(exc)[:200]}
