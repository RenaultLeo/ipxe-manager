"""Contrôles d'intégrité (HTTP + audit exhaustif) pour la page Supervision."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Request

from app.config import settings
from app.database import SessionLocal
from app.models.models import AppSetting
from app.services.server_diagnostics import (
    PROJECT_ROOT,
    _detect_venv,
    check_celery,
    check_database,
    check_paths,
    check_port_open,
    check_redis,
    http_probe,
    http_raw_get,
    systemctl_is_active,
    DEFAULT_SYSTEMD_UNITS,
)

logger = logging.getLogger(__name__)

_LAST_VER_KEY = "supervision_last_verification"
_last_run: dict[str, Any] | None = None


def persist_last_verification(result: dict[str, Any]) -> None:
    global _last_run
    _last_run = result
    try:
        payload = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning("Vérification non sérialisable : %s", exc)
        return
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _LAST_VER_KEY).first()
        if row:
            row.value = payload
        else:
            db.add(AppSetting(key=_LAST_VER_KEY, value=payload))
        db.commit()
    except Exception:
        logger.exception("persist_last_verification")
        db.rollback()
    finally:
        db.close()


def get_last_verification() -> dict[str, Any] | None:
    """Dernier audit (SQLite) — partagé entre workers Uvicorn/Gunicorn."""
    global _last_run
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _LAST_VER_KEY).first()
        if row and (row.value or "").strip():
            data = json.loads(row.value)
            if isinstance(data, dict):
                _last_run = _normalize_verification_result(data)
                return _last_run
    except Exception:
        logger.exception("get_last_verification")
    finally:
        db.close()
    if _last_run is not None:
        return _normalize_verification_result(_last_run)
    return None


def _normalize_verification_result(data: dict[str, Any]) -> dict[str, Any]:
    """Compat : anciennes entrées utilisaient la clé « items » (conflit Jinja dict.items)."""
    if "checks" not in data and "items" in data:
        data = {**data, "checks": data["items"]}
    return data


def _session_cookie_from_request(request: Request) -> str:
    raw = request.cookies.get("session", "")
    if not raw:
        return ""
    if "=" in raw and raw.lower().startswith("session="):
        return raw
    return f"session={raw}"


def _base_url_for_request(request: Request) -> str:
    base = settings.server_base_url.rstrip("/")
    if base and not base.startswith("http://127.0.0.1"):
        return base
    return str(request.base_url).rstrip("/")


def run_quick_verification(request: Request) -> dict[str, Any]:
    """Vérification rapide : services, ports, chemins, pages clés avec session."""
    started = time.time()
    base = _base_url_for_request(request)
    cookie = _session_cookie_from_request(request)
    items: list[dict[str, Any]] = []

    def add(cat: str, name: str, ok: bool | None, detail: str = "") -> None:
        items.append(
            {
                "category": cat,
                "name": name,
                "ok": ok,
                "status": "ok" if ok else ("unknown" if ok is None else "error"),
                "detail": detail[:300],
            }
        )

    for unit in DEFAULT_SYSTEMD_UNITS:
        info = systemctl_is_active(unit)
        add("Services", unit, info.get("active"), info.get("state", ""))

    for proto, port, label in (("tcp", 80, "HTTP"), ("tcp", 6379, "Redis"), ("udp", 69, "TFTP")):
        open_ = check_port_open(proto, port)
        add("Réseau", label, open_, f"port {port}")

    redis = check_redis()
    add("Stack", "Redis ping", redis.get("ok"), str(redis.get("detail", "")))

    celery = check_celery(PROJECT_ROOT, _detect_venv())
    add("Stack", "Celery inspect", celery.get("ok"), " ".join(celery.get("detail", [])[:2]) if isinstance(celery.get("detail"), list) else str(celery.get("detail", "")))

    db = check_database()
    add("Stack", "Base de données", db.get("ok"), str(db.get("detail", "")))

    for p in check_paths()[:5]:
        add("Fichiers", p["label"], p["status"] == "ok", p["path"])

    # Même sonde TLS que les requêtes avec session (certificat interne auto-signé)
    login = http_probe(base, "/login", insecure_tls=True)
    add("HTTP", "GET /login", login.get("ok"), login.get("detail", ""))

    if cookie:
        for path, title in (
            ("/", "Tableau de bord"),
            ("/isos", "ISOs"),
            ("/admin/supervision", "Supervision"),
            ("/settings", "Paramètres"),
        ):
            code, body = http_raw_get(
                base, path, cookie=cookie, timeout=20.0, insecure_tls=True
            )
            ok = code == 200 and b"<html" in body.lower()
            add("HTTP (session)", title, ok, f"HTTP {code}")
    else:
        add("HTTP (session)", "Cookie session", False, "Session absente")

    failures = sum(1 for i in items if i["ok"] is False)
    result = {
        "mode": "quick",
        "started_at": started,
        "duration_sec": round(time.time() - started, 2),
        "base_url": base,
        "checks": items,
        "failures": failures,
        "warnings": 0,
        "ok": failures == 0,
        "log": "",
    }
    persist_last_verification(result)
    return result


def run_full_exhaustive(request: Request) -> dict[str, Any]:
    """Lance scripts/ipxe_exhaustive_check.py avec le cookie de session admin."""
    started = time.time()
    base = _base_url_for_request(request)
    cookie = _session_cookie_from_request(request)
    script = PROJECT_ROOT / "scripts" / "ipxe_exhaustive_check.py"
    venv = _detect_venv()

    if not script.is_file():
        result = {
            "mode": "full",
            "ok": False,
            "failures": 1,
            "log": f"Script introuvable : {script}",
            "checks": [],
            "duration_sec": 0,
            "base_url": base,
        }
        persist_last_verification(result)
        return result

    if not cookie:
        result = {
            "mode": "full",
            "ok": False,
            "failures": 1,
            "log": "Session admin requise pour l’audit exhaustif.",
            "checks": [],
            "duration_sec": 0,
            "base_url": base,
        }
        persist_last_verification(result)
        return result

    cmd = [
        sys.executable,
        str(script),
        "--base-url",
        base,
        "--session-cookie",
        cookie,
        "--full-local",
        "--systemd",
        "--check-redis",
        "--celery-inspect",
        "--strict-menus",
        "--app-dir",
        str(PROJECT_ROOT),
        "--venv",
        str(venv),
    ]
    exit_code = -1
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(PROJECT_ROOT),
        )
        exit_code = proc.returncode
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        ok = proc.returncode == 0
        failures = 0 if ok else max(1, proc.returncode)
    except subprocess.TimeoutExpired:
        log = "Audit interrompu (timeout 10 min)."
        ok = False
        failures = 1
    except Exception as exc:
        log = str(exc)
        ok = False
        failures = 1

    result = {
        "mode": "full",
        "started_at": started,
        "duration_sec": round(time.time() - started, 2),
        "base_url": base,
        "ok": ok,
        "failures": failures,
        "exit_code": exit_code,
        "log": log[-50000:],
        "checks": _parse_log_categories(log),
    }
    persist_last_verification(result)
    return result


def _parse_log_categories(log: str) -> list[dict[str, Any]]:
    """Extrait un résumé par catégorie depuis la sortie du script."""
    items: list[dict[str, Any]] = []
    for line in log.splitlines():
        line = line.strip()
        if line.startswith("✗ ") or line.startswith("✓ ") or line.startswith("⚠ "):
            ok = line.startswith("✓")
            warn = line.startswith("⚠")
            items.append(
                {
                    "category": "Audit",
                    "name": line[2:120],
                    "ok": ok if not warn else None,
                    "status": "ok" if ok else ("warn" if warn else "error"),
                    "detail": "",
                }
            )
    return items[-80:]
