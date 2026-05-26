"""Collecte d'état serveur pour la page admin Supervision (lecture seule)."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_SNAPSHOT_CACHE: tuple[float, dict[str, Any]] | None = None
_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_TTL_SEC = 20.0

_DIR_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_DIR_SIZE_LOCK = threading.Lock()
_DIR_SIZE_TTL_SEC = 300.0
SERVICE_CTL = Path("/usr/local/sbin/ipxe-service-ctl")


def _systemctl_bin() -> str:
    return shutil.which("systemctl") or "/usr/bin/systemctl"

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
    """Essaie sudo -n puis systemctl direct (chemin absolu pour sudoers)."""
    sc = _systemctl_bin()
    base = list(args)
    return [
        ["sudo", "-n", sc, *base],
        [sc, *base],
    ]


def systemctl_is_active(unit: str) -> dict[str, Any]:
    state = "unknown"
    detail = ""
    used_sudo = False
    for cmd in _systemctl_argv("is-active", unit):
        code, out, err = _run_cmd(cmd, timeout=4)
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


RESTART_UNITS_ORDER = ("ipxe-celery", "tftpd-hpa", "ipxe-manager")


def schedule_service_restarts() -> None:
    """
    Relance les services dans un processus détaché (après la réponse HTTP).
    ipxe-manager en dernier pour éviter de tuer la requête en cours.
    """
    ctl = str(SERVICE_CTL) if SERVICE_CTL.is_file() else ""
    sc = _systemctl_bin()
    lines = []
    for unit in RESTART_UNITS_ORDER:
        if ctl:
            lines.append(f"sudo -n {ctl} restart {unit} || true")
        else:
            lines.append(f"sudo -n {sc} restart {unit} || true")
    script = "sleep 0.4\n" + "\n".join(lines) + "\n"
    try:
        subprocess.Popen(
            ["/bin/bash", "-c", script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Relance services planifiée (processus détaché)")
    except OSError:
        logger.exception("Impossible de planifier la relance des services")


def check_port_open(proto: str, port: int, host: str = "127.0.0.1") -> bool | None:
    try:
        if proto == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            try:
                s.connect((host, port))
                return True
            except OSError:
                return False
            finally:
                s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
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


def check_celery(
    app_dir: Path | None = None,
    venv: Path | None = None,
    *,
    timeout: float = 25,
) -> dict[str, Any]:
    app_dir = app_dir or PROJECT_ROOT
    venv = venv or _detect_venv()
    celery_bin = venv / "bin" / "celery"
    if not celery_bin.is_file():
        return {"ok": None, "detail": f"Celery absent : {celery_bin}", "status": "unknown"}
    code, out, err = _run_cmd(
        [str(celery_bin), "-A", "app.tasks.celery_app", "inspect", "ping"],
        timeout=timeout,
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


def check_database(*, quick: bool = False) -> dict[str, Any]:
    url = settings.database_url
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1] if "///" in url else url.replace("sqlite:///", "")
        p = Path(path)
        if not p.is_file():
            return {"ok": False, "engine": "sqlite", "detail": f"fichier absent : {p}", "status": "error"}
        if quick:
            return {
                "ok": True,
                "engine": "sqlite",
                "detail": "fichier présent",
                "status": "ok",
            }
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


def get_dir_size_cached(path: str | Path) -> int | None:
    """Taille répertoire avec cache (évite rglob à chaque chargement Supervision)."""
    key = str(path)
    now = time.monotonic()
    with _DIR_SIZE_LOCK:
        hit = _DIR_SIZE_CACHE.get(key)
        if hit and now - hit[0] < _DIR_SIZE_TTL_SEC:
            return hit[1]
    try:
        size = get_dir_size(path)
    except OSError:
        logger.exception("get_dir_size_cached %s", key)
        return None
    with _DIR_SIZE_LOCK:
        _DIR_SIZE_CACHE[key] = (now, size)
    return size


def check_paths(*, probe_writes: bool = True) -> list[dict[str, Any]]:
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
        if exists and probe_writes:
            try:
                test = p / ".write_probe"
                test.touch()
                test.unlink(missing_ok=True)
                writable = True
            except OSError:
                writable = False
        elif exists:
            writable = True
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


def host_metrics(*, cpu_interval: float = 0.15) -> dict[str, Any]:
    try:
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=cpu_interval)
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


def remote_server_reachability(base_url: str, *, probe: bool = True, timeout: float = 4.0) -> dict[str, Any]:
    """Joignabilité HTTP du serveur PXE (URL publique configurée)."""
    from urllib.parse import urlparse

    url = (base_url or "").strip().rstrip("/")
    p = urlparse(url)
    name = p.hostname or url or "—"
    if p.port and p.port not in (80, 443):
        name = f"{name}:{p.port}"
    if not probe or not url:
        return {
            "name": name,
            "url": url,
            "online": None,
            "status": "unknown",
        }
    probe_result = http_probe(url, "/login", timeout=timeout, insecure_tls=True)
    online = bool(probe_result.get("ok"))
    return {
        "name": name,
        "url": url,
        "online": online,
        "status": "ok" if online else "error",
    }


def build_machines_list(
    host: dict[str, Any],
    server_base_url: str,
    remote: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    local_name = (host.get("hostname") or "localhost").strip()
    remote = remote or remote_server_reachability(server_base_url)
    return [
        {
            "id": "local",
            "name": local_name,
            "online": True,
            "status": "ok",
            "kind": "local",
        },
        {
            "id": "remote",
            "name": remote["name"],
            "online": remote["online"],
            "status": remote["status"],
            "kind": "remote",
            "url": remote.get("url", ""),
        },
    ]


def application_stats(db: Session, *, include_dir_sizes: bool = True) -> dict[str, Any]:
    from app.models.models import IsoVersion, Upload, User

    users = db.query(User).count()
    isos = db.query(IsoVersion).count()
    uploads_pending = db.query(Upload).filter(Upload.status.in_(["pending", "processing"])).count()
    extracting = db.query(IsoVersion).filter(IsoVersion.status == "extracting").count()
    http_disk = get_disk_usage()
    iso_bytes = get_dir_size_cached(settings.iso_root) if include_dir_sizes else None
    http_bytes = get_dir_size_cached(settings.http_root) if include_dir_sizes else None
    base = settings.server_base_url.rstrip("/")
    return {
        "users": users,
        "iso_versions": isos,
        "uploads_in_progress": uploads_pending,
        "iso_extracting": extracting,
        "server_base_url": base,
        "http_disk": http_disk,
        "iso_dir_size": fmt_size(iso_bytes) if iso_bytes is not None else None,
        "http_dir_size": fmt_size(http_bytes) if http_bytes is not None else None,
    }


def _collect_services(extended_units: bool) -> list[dict[str, Any]]:
    units = EXTENDED_SYSTEMD_UNITS if extended_units else DEFAULT_SYSTEMD_UNITS
    seen: set[str] = set()
    unique: list[str] = []
    for unit in units:
        if unit not in seen:
            seen.add(unit)
            unique.append(unit)

    def _one(unit: str) -> dict[str, Any] | None:
        info = systemctl_is_active(unit)
        if info["state"] == "unavailable" and unit.startswith("nfs-kernel"):
            return None
        return info

    workers = min(8, max(1, len(unique)))
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, u): u for u in unique}
        for fut in as_completed(futures):
            try:
                row = fut.result()
                if row:
                    out.append(row)
            except Exception:
                logger.exception("systemctl %s", futures[fut])
    out.sort(key=lambda s: s.get("unit", ""))
    return out


def _collect_ports() -> list[dict[str, Any]]:
    def _one(item: tuple[str, int, str]) -> dict[str, Any]:
        proto, port, label = item
        open_ = check_port_open(proto, port)
        return {
            "proto": proto,
            "port": port,
            "label": label,
            "open": open_,
            "status": _status_from_bool(open_, warn_if_false=True),
        }

    with ThreadPoolExecutor(max_workers=min(8, len(MONITORED_PORTS))) as pool:
        return list(pool.map(_one, MONITORED_PORTS))


def collect_snapshot(
    db: Session,
    *,
    extended_units: bool = True,
    quick: bool = False,
    probe_remote: bool = True,
    include_dir_sizes: bool = True,
) -> dict[str, Any]:
    venv = _detect_venv()
    celery_timeout = 4.0 if quick else 12.0

    def _checks() -> list[dict[str, Any]]:
        def _redis() -> dict[str, Any]:
            return {"id": "redis", "label": "Redis", **check_redis()}

        def _celery() -> dict[str, Any]:
            return {
                "id": "celery",
                "label": "Celery",
                **check_celery(PROJECT_ROOT, venv, timeout=celery_timeout),
            }

        def _db() -> dict[str, Any]:
            return {
                "id": "database",
                "label": "Base de données",
                **check_database(quick=quick),
            }

        fns = [_redis, _celery, _db]
        with ThreadPoolExecutor(max_workers=3) as pool:
            return list(pool.map(lambda fn: fn(), fns))

    services = _collect_services(extended_units)
    ports = _collect_ports()
    paths = check_paths(probe_writes=not quick)
    checks = _checks()

    svc_active = sum(1 for s in services if s.get("active"))
    svc_total = len(services)
    port_open = sum(1 for p in ports if p.get("open"))
    host = host_metrics(cpu_interval=0.1 if quick else 0.2)
    app = application_stats(db, include_dir_sizes=include_dir_sizes)
    base_url = app.get("server_base_url", "")
    remote_probe = remote_server_reachability(
        base_url,
        probe=probe_remote,
        timeout=3.0 if quick else 5.0,
    )
    machines = build_machines_list(host, base_url, remote_probe)

    return {
        "generated_at": _utc_now(),
        "host": host,
        "application": app,
        "machines": machines,
        "remote_server": remote_probe,
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


def collect_snapshot_cached(
    db: Session,
    *,
    force: bool = False,
    quick: bool = False,
) -> dict[str, Any]:
    """Snapshot avec cache court (évite de bloquer chaque refresh Supervision)."""
    global _SNAPSHOT_CACHE
    now = time.monotonic()
    with _SNAPSHOT_LOCK:
        if (
            not force
            and _SNAPSHOT_CACHE
            and now - _SNAPSHOT_CACHE[0] < _SNAPSHOT_TTL_SEC
        ):
            return _SNAPSHOT_CACHE[1]
    snap = collect_snapshot(
        db,
        quick=quick,
        probe_remote=not quick,
        include_dir_sizes=not quick,
    )
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE = (now, snap)
    return snap


def invalidate_snapshot_cache() -> None:
    global _SNAPSHOT_CACHE
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE = None


def _probe_sudo_systemctl() -> bool:
    if SERVICE_CTL.is_file():
        code, out, _ = _run_cmd(["sudo", "-n", "-l"], timeout=5)
        return code == 0 and "ipxe-service-ctl" in (out or "")
    code, _, _ = _run_cmd(["sudo", "-n", _systemctl_bin(), "is-active", "nginx"], timeout=5)
    return code == 0


def probe_full_url(url: str, timeout: float = 3.0) -> dict[str, Any]:
    """Sonde HTTP GET sur une URL complète (chainload iPXE distant, etc.)."""
    raw = (url or "").strip()
    if not raw:
        return {"ok": False, "status": -1, "detail": "URL vide"}
    if "://" not in raw:
        raw = "http://" + raw
    p = urlparse(raw)
    if not p.scheme or not p.hostname:
        return {"ok": False, "status": -1, "detail": "URL invalide"}
    path = p.path or "/"
    if p.query:
        path = f"{path}?{p.query}"
    port = p.port or (443 if p.scheme == "https" else 80)
    base = f"{p.scheme}://{p.hostname}"
    if port not in (80, 443):
        base = f"{base}:{port}"
    return http_probe(base, path, timeout=timeout)


def chain_url_reachable(url: str, timeout: float = 3.0) -> bool:
    """Serveur joignable si une réponse HTTP est reçue (code > 0), pas seulement 200."""
    result = probe_full_url(url, timeout=timeout)
    code = result.get("status", -1)
    if isinstance(code, int) and code > 0:
        return True
    return bool(result.get("ok"))


def probe_urls_parallel(urls: list[tuple[int, str]], timeout: float = 3.0) -> dict[int, bool]:
    """Sonde plusieurs URLs en parallèle ; retourne {id: online}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[int, bool] = {}
    if not urls:
        return out

    def _one(item: tuple[int, str]) -> tuple[int, bool]:
        chain_id, url = item
        return chain_id, chain_url_reachable(url, timeout=timeout)

    workers = min(8, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, item) for item in urls]
        for fut in as_completed(futures):
            try:
                chain_id, online = fut.result()
                out[chain_id] = online
            except Exception:
                logger.exception("probe_urls_parallel")
    return out


def _https_ssl_context(*, insecure_tls: bool):
    import ssl

    ctx = ssl.create_default_context()
    if insecure_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_raw_get(
    base_url: str,
    path: str,
    *,
    cookie: str = "",
    timeout: float = 8.0,
    insecure_tls: bool = False,
) -> tuple[int, bytes]:
    """GET HTTP(S) brut ; retourne (code, corps). code -1 si connexion impossible."""
    import http.client

    p = urlparse(base_url)
    if not p.scheme or not p.hostname:
        return -1, b""
    port = p.port or (443 if p.scheme == "https" else 80)
    headers = {"User-Agent": "ipxe-supervision/1.0"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        if p.scheme == "https":
            conn = http.client.HTTPSConnection(
                p.hostname,
                port,
                timeout=timeout,
                context=_https_ssl_context(insecure_tls=insecure_tls),
            )
        else:
            conn = http.client.HTTPConnection(p.hostname, port, timeout=timeout)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        code = resp.status
        conn.close()
        return code, body
    except Exception:
        return -1, b""


def http_probe(
    base_url: str,
    path: str = "/login",
    timeout: float = 8.0,
    *,
    insecure_tls: bool = False,
) -> dict[str, Any]:
    code, _body = http_raw_get(
        base_url,
        path,
        timeout=timeout,
        insecure_tls=insecure_tls,
    )
    if code < 0:
        return {
            "ok": False,
            "status": -1,
            "detail": "Connexion impossible (réseau ou certificat TLS)",
        }
    ok = code in (200, 301, 302, 303, 307, 308)
    return {"ok": ok, "status": code, "detail": f"HTTP {code}"}
