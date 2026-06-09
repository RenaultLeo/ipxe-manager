#!/usr/bin/env python3
"""
Smoke + charge légère pour iPXE Manager (sans dépendances hors stdlib).

Vérifie que l’HTTP répond comme attendu, puis enchaîne des requêtes concurrentes.

Usage production (via Nginx) :
  python3 scripts/ipxe_health_load.py --base-url http://192.168.2.6

Usage direct uvicorn :
  python3 scripts/ipxe_health_load.py --base-url http://127.0.0.1:8000 --skip-menus

Charge :
  python3 scripts/ipxe_health_load.py --base-url http://192.168.2.6 --workers 30 --requests 500

Contrôles optionnels (sur le serveur applicatif) :
  python3 scripts/ipxe_health_load.py --check-redis
  python3 scripts/ipxe_health_load.py --celery-inspect --app-dir /srv/ipxe/app --venv /srv/ipxe/venv
"""
from __future__ import annotations

import argparse
import http.client
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, urljoin, urlparse

USER_AGENT = "ipxe-health-load/1.0"
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _is_http_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname)


def _req_raw(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, float, str | None]:
    """Requête HTTP sans suivre les redirections (client bas niveau)."""
    if not _is_http_url(url):
        return -1, 0.0, None
    p = urlparse(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    path = p.path or "/"
    if p.query:
        path = path + "?" + p.query

    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    if data is not None and "Content-Type" not in headers:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    t0 = time.perf_counter()
    conn: http.client.HTTPConnection | http.client.HTTPSConnection | None = None
    try:
        if p.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(p.hostname, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(p.hostname, port, timeout=timeout)
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        _ = r.read()
        loc = r.getheader("Location")
        return r.status, time.perf_counter() - t0, loc
    except OSError:
        return -1, time.perf_counter() - t0, None
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def _req(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    timeout: float = 30.0,
    no_redirect: bool = False,
    max_redirects: int = 8,
) -> tuple[int, float, str | None]:
    """Retourne (status, durée_s, location si redirect)."""
    if not _is_http_url(url):
        return -1, 0.0, None
    if no_redirect:
        return _req_raw(url, method=method, data=data, timeout=timeout)

    current_url = url
    current_method = method
    current_data = data
    total_dt = 0.0
    for _ in range(max_redirects + 1):
        code, dt, loc = _req_raw(
            current_url,
            method=current_method,
            data=current_data,
            timeout=timeout,
        )
        total_dt += dt
        if code in _REDIRECT_CODES and loc:
            next_url = urljoin(current_url, loc)
            if not _is_http_url(next_url):
                return code, total_dt, loc
            current_url = next_url
            if code in (301, 302, 303):
                current_method = "GET"
                current_data = None
            continue
        return code, total_dt, loc
    return -1, total_dt, None


def smoke(base: str, timeout: float, skip_menus: bool) -> int:
    """Retourne nombre d’échecs critiques."""
    fails = 0
    print("=== Smoke (HTTP) ===")

    url = urljoin(base, "/login")
    code, dt, _ = _req(url, timeout=timeout)
    ok = code == 200
    print(f"  {'✓' if ok else '✗'} GET /login  → {code}  ({dt*1000:.1f} ms)")
    if not ok:
        fails += 1

    url = urljoin(base, "/")
    code, dt, loc = _req(url, timeout=timeout, no_redirect=True)
    ok = code in (301, 302, 303, 307, 308) and loc and "login" in loc
    print(f"  {'✓' if ok else '✗'} GET /       → {code}  Location={loc!r}  ({dt*1000:.1f} ms)")
    if not ok:
        fails += 1

    url = urljoin(base, "/static/css/custom.css")
    code, dt, _ = _req(url, timeout=timeout)
    ok = code == 200
    print(f"  {'✓' if ok else '✗'} GET /static/css/custom.css → {code}  ({dt*1000:.1f} ms)")
    if not ok:
        fails += 1

    if not skip_menus:
        url = urljoin(base, "/menus/menu.ipxe")
        code, dt, _ = _req(url, timeout=timeout)
        if code == 200:
            print(f"  ✓ GET /menus/menu.ipxe → 200 ({dt*1000:.1f} ms)")
        elif code == 404:
            print(
                f"  ⚠ GET /menus/menu.ipxe → 404 ({dt*1000:.1f} ms)"
                " — normal si vous testez uvicorn sans Nginx,"
                " ou si aucun menu n’a encore été généré."
            )
        else:
            print(f"  ⚠ GET /menus/menu.ipxe → {code} ({dt*1000:.1f} ms)")

    url = urljoin(base, "/login")
    body = urlencode({"password": "__load_test_bad__"}).encode()
    code, dt, _ = _req(url, method="POST", data=body, timeout=timeout)
    ok = code == 401
    print(f"  {'✓' if ok else '✗'} POST /login (mot de passe incorrect) → {code} ({dt*1000:.1f} ms)")
    if not ok:
        fails += 1

    print()
    return fails


def load_phase(
    base: str,
    path: str,
    workers: int,
    total: int,
    timeout: float,
) -> int:
    """Retourne nombre d’échecs (status hors 2xx/3xx)."""
    url = urljoin(base, path)
    print(f"=== Charge : {total} requêtes, {workers} threads, cible {path} ===")

    latencies: list[float] = []
    errors = 0
    ok = 0

    def one() -> tuple[float, bool]:
        code, dt, _ = _req(url, timeout=timeout)
        good = 200 <= code < 400
        return dt, good

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one) for _ in range(total)]
        for fu in as_completed(futs):
            dt, good = fu.result()
            latencies.append(dt)
            if good:
                ok += 1
            else:
                errors += 1
    wall = time.perf_counter() - t0

    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2] if n else 0
    p95 = latencies[int(n * 0.95)] if n > 0 else 0

    print(f"  Durée totale   : {wall:.2f} s  (~{total/wall:.1f} req/s)")
    print(f"  Réussites 2–3xx: {ok} / {total}")
    print(f"  Échecs         : {errors}")
    if n:
        print(
            f"  Latence        : min {min(latencies)*1000:.1f} ms | "
            f"p50 {p50*1000:.1f} ms | p95 {p95*1000:.1f} ms | max {max(latencies)*1000:.1f} ms"
        )
    print()
    return errors


def check_redis_cli() -> bool:
    print("=== Redis (redis-cli ping) ===")
    try:
        out = subprocess.run(
            ["redis-cli", "ping"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        print("  ⚠ redis-cli absent — installez redis-tools ou ignorez --check-redis.")
        print()
        return True
    ok = out.returncode == 0 and "PONG" in (out.stdout or "").upper()
    print(f"  {'✓' if ok else '✗'} {(out.stdout or out.stderr or '').strip()}")
    print()
    return ok


def check_celery(app_dir: str, venv: str) -> bool:
    print("=== Celery (inspect ping) ===")
    celery_bin = f"{venv.rstrip('/')}/bin/celery"
    try:
        out = subprocess.run(
            [celery_bin, "-A", "app.tasks.celery_app", "inspect", "ping"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        print(f"  ✗ binaire introuvable : {celery_bin}")
        print()
        return False
    txt = (out.stdout or "") + (out.stderr or "")
    ok = out.returncode == 0 and "pong" in txt.lower()
    print(f"  {'✓' if ok else '✗'} exit={out.returncode}")
    if txt.strip():
        print("  ---")
        for line in txt.strip().splitlines()[:12]:
            print(f"  {line}")
        if len(txt.splitlines()) > 12:
            print("  …")
    print()
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke + charge HTTP pour iPXE Manager")
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1",
        help="URL de base (Nginx en prod, ex. http://192.168.2.6)",
    )
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout HTTP (s)")
    p.add_argument("--skip-menus", action="store_true", help="Ne pas tester /menus/menu.ipxe")
    p.add_argument("--workers", type=int, default=20, help="Threads pour la phase charge")
    p.add_argument("--requests", type=int, default=200, help="Nombre de requêtes en charge")
    p.add_argument(
        "--load-path",
        default="/login",
        help="Chemin cible pour la charge (défaut : page login, léger)",
    )
    p.add_argument("--no-load", action="store_true", help="Uniquement le smoke")
    p.add_argument("--check-redis", action="store_true", help="Exécute redis-cli ping (optionnel)")
    p.add_argument("--celery-inspect", action="store_true", help="Celery inspect ping (serveur app)")
    p.add_argument("--app-dir", default="/srv/ipxe/app", help="Répertoire app pour Celery")
    p.add_argument("--venv", default="/srv/ipxe/venv", help="Virtualenv pour binaire celery")
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        print("Erreur : --base-url invalide (ex. http://192.168.2.6)", file=sys.stderr)
        return 2

    print(f"Cible : {base}\n")

    bad = smoke(base, args.timeout, args.skip_menus)

    if args.check_redis:
        if not check_redis_cli():
            bad += 1

    if args.celery_inspect:
        if not check_celery(args.app_dir, args.venv):
            bad += 1

    if not args.no_load:
        bad += load_phase(base, args.load_path, args.workers, args.requests, args.timeout)

    if bad:
        print(f"Résultat : {bad} problème(s) détecté(s) — code sortie 1.")
        return 1
    print("Résultat : OK — code sortie 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
