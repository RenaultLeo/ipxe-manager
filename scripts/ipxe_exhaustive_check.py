#!/usr/bin/env python3
"""
Audit HTTP étendu pour iPXE Manager (stdlib uniquement).

- Pages publiques : /login, redirections racine, statiques, /set-language, POST login invalide.
- Menus Nginx /menus/menu.ipxe : statut HTTP + validates iPXE (#!ipxe, directives menu/item).
- Avec mot de passe admin : cookie de session, puis toutes les pages UI principales (200 HTML),
  API JSON léger (/isos/upload/precheck), image Paramètres, etc.
- Optionnel : systemd (Linux), redis-cli ping, celery inspect ping.

Exemple :

  python3 scripts/ipxe_exhaustive_check.py --base-url http://192.168.2.6 --password secretdemo

  IPXE_AUDIT_PASSWORD=secret python3 scripts/ipxe_exhaustive_check.py \\
      --base-url http://127.0.0.1:8000 --skip-menus --include-openapi

Codes sortie : 0 tout OK · 1 échecs · 2 arguments ou login refusé
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import re
import ssl
import subprocess
import sys
from collections.abc import Iterable
from urllib.parse import urlencode, urlparse

USER_AGENT = "ipxe-exhaustive-check/1.0"

LOGIN_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _is_root_location(location: str) -> bool:
    """True si Location pointe vers la racine de l'hôte (/ ou URL absolue terminée par / sans path)."""
    if not location:
        return False
    p = urlparse(location)
    path = (p.path or "").rstrip("/") or ""
    return path == ""


def _is_login_location(location: str) -> bool:
    if not location:
        return False
    lp = urlparse(location)
    pn = lp.path or ""
    return "login" in pn


def _conn_for_url(parsed: urlparse, timeout: float, insecure: bool):
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname
    assert host is not None
    if parsed.scheme == "https":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _cookie_header_from_response(resp: http.client.HTTPResponse) -> str:
    parts: list[str] = []
    for k, v in resp.getheaders():
        if k.lower() == "set-cookie":
            parts.append(v.split(";", 1)[0].strip())
    return "; ".join(parts)


def http_request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    insecure: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    """
    Une requête HTTP sans redirection automatique. Retourne (status, headers lower-key dédoublonnés dernier vainqueur,
    corps complet — pages UI restent petites).
    """
    p = urlparse(base)
    if not p.scheme or not p.hostname:
        return -1, {}, b""

    hpath = path if path.startswith("/") else "/" + path
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    conn = _conn_for_url(p, timeout, insecure)
    try:
        conn.request(method, hpath, body=data, headers=hdrs)
        r = conn.getresponse()
        raw_headers = [(k.lower(), v) for k, v in r.getheaders()]
        mh: dict[str, str] = {k: v for k, v in raw_headers}
        body = r.read()
        return r.status, mh, body
    except OSError:
        return -1, {}, b""
    finally:
        try:
            conn.close()
        except OSError:
            pass


def post_login_cookie(base: str, password: str, timeout: float, insecure: bool) -> tuple[int, str]:
    """POST /login avec mot de passe. Retourne (status, cookie header ou '')."""
    body = urlencode({"password": password}).encode()
    p = urlparse(base)
    if not p.scheme or not p.hostname:
        return -1, ""

    conn = _conn_for_url(p, timeout, insecure)
    try:
        path = "/login"
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r = conn.getresponse()
        _ = r.read()
        ck = _cookie_header_from_response(r)
        return r.status, ck
    except OSError:
        return -1, ""
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _brief(text: bytes, n: int = 200) -> str:
    try:
        s = text.decode("utf-8", errors="replace")
    except Exception:
        return repr(text[:80])
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:n] + "…") if len(s) > n else s


class Audit:
    def __init__(
        self,
        base: str,
        *,
        timeout: float,
        insecure: bool,
        strict_menus: bool,
        skip_menus: bool,
        cookie: str = "",
        include_openapi: bool,
    ):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.insecure = insecure
        self.strict_menus = strict_menus
        self.skip_menus = skip_menus
        self.cookie = cookie
        self.include_openapi = include_openapi
        self.failures = 0
        self.warnings = 0

    def _hdr(self, with_cookie: bool) -> dict[str, str]:
        if with_cookie and self.cookie:
            return {"Cookie": self.cookie}
        return {}

    def ok(self, cond: bool, msg: str) -> None:
        print(f"  {'✓' if cond else '✗'} {msg}")
        if not cond:
            self.failures += 1

    def warn(self, cond: bool, msg: str) -> None:
        print(f"  {'✓' if cond else '⚠'} {msg}")
        if not cond:
            self.warnings += 1

    def check_get(
        self,
        path: str,
        expect_status: Iterable[int],
        *,
        auth: bool = False,
        name: str | None = None,
        substring: bytes | None = None,
        is_png: bool = False,
    ) -> None:
        nm = name or path
        code, hdrs, body = http_request(
            self.base,
            path,
            timeout=self.timeout,
            insecure=self.insecure,
            headers=self._hdr(auth),
        )
        exp = frozenset(expect_status)
        self.ok(code in exp, f"{nm} → HTTP {code} (attendu {sorted(exp)})")
        if code in exp and substring is not None and substring not in body:
            self.ok(False, f"{nm} : corps sans séquence attendue {substring!r} ({_brief(body)})")
        if code in exp and is_png:
            png_ok = body[:8] == b"\x89PNG\r\n\x1a\n"
            self.ok(png_ok, f"{nm} : en-tête PNG")

    def check_redirect(self, path: str, *, must_include: str | None = "login", name: str | None = None) -> None:
        nm = name or path
        code, hdrs, _ = http_request(self.base, path, timeout=self.timeout, insecure=self.insecure)
        loc = hdrs.get("location", "") or ""
        ok = code in LOGIN_REDIRECT_CODES
        ok = ok and (must_include is None or must_include in loc.lower())
        self.ok(ok, f"{nm} → {code} Location={loc!r}")


def run_public_phase(audit: Audit) -> None:
    print("=== Public (sans session) ===")
    audit.check_get("/login", (200,))
    audit.check_redirect("/", must_include="login")
    audit.check_redirect("/isos", must_include="login")
    audit.check_redirect("/boot-files", must_include="login")
    audit.check_redirect("/ipxe-configs", must_include="login")
    audit.check_redirect("/ipxe-menus", must_include="login")
    audit.check_redirect("/firmware", must_include="login")
    audit.check_redirect("/settings", must_include="login")

    code_d, hdrs_d, _ = http_request(audit.base, "/set-language", timeout=audit.timeout, insecure=audit.insecure)
    loc_d = hdrs_d.get("location", "") or ""
    ok_d = code_d in LOGIN_REDIRECT_CODES and _is_root_location(loc_d)
    audit.ok(
        ok_d,
        f"GET /set-language (défaut) → {code_d} Location={loc_d!r} "
        "(attendu 3xx vers / ou URL racine après cookie locale)",
    )

    code_r, hdrs_r, body_r = http_request(
        audit.base,
        "/set-language?lang=en&next=%2Fisos",
        timeout=audit.timeout,
        insecure=audit.insecure,
    )
    loc_r = hdrs_r.get("location", "") or ""
    path_r = (urlparse(loc_r).path or "").rstrip("/")
    ok_r = code_r in LOGIN_REDIRECT_CODES and path_r.endswith("isos")
    audit.ok(ok_r, f"GET /set-language?lang=en&next=/isos → {code_r} Location={loc_r!r}")

    code_kill, hdrs_kill, _ = http_request(audit.base, "/jobs/kill-all", timeout=audit.timeout, insecure=audit.insecure)
    loc_kill = hdrs_kill.get("location", "") or ""
    audit.ok(
        code_kill in LOGIN_REDIRECT_CODES and _is_root_location(loc_kill),
        f"GET /jobs/kill-all (sans session) → {code_kill} Location={loc_kill!r}",
    )

    audit.check_get("/static/css/custom.css", (200,), substring=b"iPXE Manager")
    audit.check_get("/static/js/app.js", (200,))

    bad = urlencode({"password": "__audit_bad_pw__"}).encode()
    code, _, b = http_request(
        audit.base,
        "/login",
        method="POST",
        data=bad,
        timeout=audit.timeout,
        insecure=audit.insecure,
    )
    audit.ok(code == 401, f"POST /login mot de passe incorrect → {code} (401 attendu)")
    login_hint = bool(re.search(rb"(?i)password|mot de passe|connexion", b))
    audit.ok(login_hint or code == 401, "POST /login (401) corps semble être la page login")

    if audit.include_openapi:
        audit.check_get("/openapi.json", (200,), substring=b'"openapi"', name="GET /openapi.json (FastAPI)")
        audit.check_get("/docs", (200,), substring=b"swagger", name="GET /docs (Swagger UI)")

    print()


def validate_menu_ipxe(body: bytes) -> tuple[bool, str]:
    if not body:
        return False, "corps vide"
    head = body.lstrip()[:200].decode("utf-8", errors="replace").lower()
    if not head.startswith("#!ipxe"):
        return False, f"début inattendu : {_brief(body, 120)}"
    blob = body.decode("utf-8", errors="replace").lower()
    if "\nmenu " not in blob and blob.count("menu ") < 1:
        return False, "pas de directive 'menu'"
    if "\nitem " not in blob and blob.count("item ") < 1:
        return False, "pas de directive 'item'"
    return True, ""


def run_menu_phase(audit: Audit) -> None:
    if audit.skip_menus:
        print("=== Menus Nginx (/menus/) — ignoré (--skip-menus) ===\n")
        return
    print("=== Menus iPXE (HTTP statique sous /menus/) ===")
    code, _, body = http_request(audit.base, "/menus/menu.ipxe", timeout=audit.timeout, insecure=audit.insecure)
    if code == 404:
        msg = (
            "GET /menus/menu.ipxe → 404 (pas encore généré depuis l’UI, ou uvicorn seul sans Nginx / alias /menus)"
        )
        if audit.strict_menus:
            audit.ok(False, msg + " (--strict-menus)")
        else:
            audit.warn(False, msg)
        print()
        return

    audit.ok(code == 200, f"GET /menus/menu.ipxe → HTTP {code} ({len(body)} octets)")
    if code == 200:
        sane, detail = validate_menu_ipxe(body)
        if sane:
            print("  ✓ Structure iPXE : #!ipxe + directives menu/item")
        else:
            audit.ok(False, f"Menu mal formé ou incomplet : {detail}")
    print()


def run_authenticated_pages(audit: Audit, password: str) -> bool:
    """Établit la session et vérifie les pages. Retourne False si login impossible."""
    if not password:
        print(
            "=== Pages authentifiées — saut (fournissez --password ou la variable "
            "d’environnement IPXE_AUDIT_PASSWORD) ===\n"
        )
        return True

    print("=== Session admin (pages HTML / JSON légers) ===")
    status, ck = post_login_cookie(audit.base, password, audit.timeout, audit.insecure)
    if status != 302 or not ck:
        audit.ok(False, f"Connexion POST /login : statut={status}, cookie défini={'oui' if ck else 'non'}")
        print()
        return False

    print(f"  ✓ POST /login → {status}, cookie session reçu")
    audit.cookie = ck

    html_paths = (
        ("/", "dashboard"),
        ("/isos", "liste ISO"),
        ("/isos/upload", "upload ISO"),
        ("/boot-files", "boot files"),
        ("/ipxe-configs", "configs"),
        ("/ipxe-configs/new", "nouvelle config"),
        ("/ipxe-menus", "menus iPXE (UI)"),
        ("/firmware", "firmware"),
        ("/settings", "paramètres"),
        ("/settings/os-types/new", "nouveau type d’OS"),
    )
    for path, title in html_paths:
        audit.check_get(path, (200,), auth=True, name=f"HTML — {title} [{path}]", substring=b"<html")

    audit.check_get(
        "/settings/bundled-menu-logo.png",
        (200,),
        auth=False,
        name="GET /settings/bundled-menu-logo.png",
        is_png=True,
    )

    code_precheck, _, precheck_body = http_request(
        audit.base,
        "/isos/upload/precheck?total_bytes=0",
        timeout=audit.timeout,
        insecure=audit.insecure,
        headers=audit._hdr(True),
    )
    pre_summary = ""
    ok_pre = False
    if code_precheck != 200:
        pre_summary = f"HTTP {code_precheck}"
    else:
        try:
            payload = json.loads(precheck_body.decode("utf-8"))
            ok_pre = payload.get("ok") is True
            pre_summary = json.dumps(payload, ensure_ascii=False)[:200]
        except json.JSONDecodeError:
            pre_summary = "corps non JSON"
    audit.ok(
        ok_pre,
        "GET /isos/upload/precheck?total_bytes=0 "
        f'→ attendu {{"ok": true}}, obtenu {code_precheck} ({pre_summary})',
    )

    code_raw, _, raw_body = http_request(
        audit.base,
        "/ipxe-menus/menu.ipxe/raw",
        timeout=audit.timeout,
        insecure=audit.insecure,
        headers=audit._hdr(True),
    )
    if code_raw == 200:
        has_shebang = raw_body.lstrip().startswith(b"#!ipxe")
        audit.ok(has_shebang, "GET /ipxe-menus/menu.ipxe/raw → 200 et commence par #!ipxe")
    elif code_raw == 404:
        audit.warn(
            False,
            "GET /ipxe-menus/menu.ipxe/raw → 404 (menu absent sur disque — régénérer depuis l’UI)",
        )
    else:
        audit.ok(False, f"GET /ipxe-menus/menu.ipxe/raw → HTTP {code_raw}")

    code_lo, hdrs_lo, _ = http_request(
        audit.base,
        "/logout",
        timeout=audit.timeout,
        insecure=audit.insecure,
        headers=audit._hdr(True),
    )
    loc_lo = hdrs_lo.get("location", "") or ""
    audit.ok(
        code_lo in LOGIN_REDIRECT_CODES and _is_login_location(loc_lo),
        f"GET /logout → {code_lo} Location={loc_lo!r}",
    )

    code_gone, hdrs_gone, _ = http_request(
        audit.base,
        "/settings",
        timeout=audit.timeout,
        insecure=audit.insecure,
        headers=audit._hdr(True),
    )
    loc_gone = hdrs_gone.get("location", "") or ""
    audit.ok(
        code_gone in LOGIN_REDIRECT_CODES and "login" in loc_gone.lower(),
        f"après logout, GET /settings → {code_gone} Location={loc_gone!r}",
    )

    print()
    audit.cookie = ""
    return True


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
        print("  ⚠ redis-cli absent.\n")
        return True
    ok = out.returncode == 0 and "PONG" in (out.stdout or "").upper()
    print(f"  {'✓' if ok else '✗'} {(out.stdout or out.stderr or '').strip()}\n")
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
        print(f"  ✗ binaire introuvable : {celery_bin}\n")
        return False
    txt = (out.stdout or "") + (out.stderr or "")
    ok = out.returncode == 0 and "pong" in txt.lower()
    print(f"  {'✓' if ok else '✗'} exit={out.returncode}")
    if txt.strip():
        for line in txt.strip().splitlines()[:12]:
            print(f"  {line}")
        if len(txt.splitlines()) > 12:
            print("  …")
    print()
    return ok


def check_systemd_units(units: list[str]) -> int:
    print("=== systemd (is-active) ===")
    fails = 0
    if platform.system() != "Linux":
        print("  ⚠ plateforme non Linux — aucun contrôle systemd.\n")
        return 0
    try:
        subprocess.run(["systemctl", "--version"], capture_output=True, timeout=3)
    except (FileNotFoundError, OSError):
        print("  ⚠ systemctl introuvable.\n")
        return 0
    for unit in units:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                capture_output=True,
                timeout=8,
            )
        except OSError:
            fails += 1
            print(f"  ✗ {unit} (erreur d’exécution)")
            continue
        ok_u = r.returncode == 0
        if not ok_u:
            fails += 1
        print(f"  {'✓' if ok_u else '✗'} {unit}")
    print()
    return fails


def main() -> int:
    p = argparse.ArgumentParser(description="Audit HTTP étendu + services pour iPXE Manager")
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1",
        help="URL de base (Nginx prod ou uvicorn, ex. http://192.168.2.6)",
    )
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout HTTP (s)")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="HTTPS : ne pas valider le certificat TLS",
    )
    p.add_argument("--password", default="", help="Mot de passe admin (sinon env IPXE_AUDIT_PASSWORD)")
    p.add_argument(
        "--skip-menus",
        action="store_true",
        help="Ne pas télécharger ni analyser /menus/menu.ipxe",
    )
    p.add_argument(
        "--strict-menus",
        action="store_true",
        help="Échouer si menu.ipxe est absent (404) — utile en prod derrière Nginx",
    )
    p.add_argument(
        "--include-openapi",
        action="store_true",
        help="Vérifier aussi /openapi.json et /docs (souvent uvicorn ; Nginx peut masquer)",
    )
    p.add_argument("--check-redis", action="store_true", help="redis-cli ping (machine locale)")
    p.add_argument("--celery-inspect", action="store_true", help="Celery inspect ping (sur le serveur app)")
    p.add_argument("--app-dir", default="/srv/ipxe/app", help="Répertoire projet pour Celery")
    p.add_argument("--venv", default="/srv/ipxe/venv", help="Virtualenv Celery")
    p.add_argument(
        "--systemd",
        action="store_true",
        help="Vérifier systemctl is-active pour les unités habituelles",
    )
    p.add_argument(
        "--systemd-unit",
        action="append",
        dest="systemd_units",
        metavar="UNIT",
        help="Unité systemd (répétable). Utilisée seule : seules ces unités sont testées ; avec --systemd sans --systemd-unit : nginx, redis-server, tftpd-hpa, ipxe-manager, ipxe-celery",
    )
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        print("Erreur : --base-url invalide (ex. http://192.168.2.6)", file=sys.stderr)
        return 2

    pw = (args.password or os.environ.get("IPXE_AUDIT_PASSWORD") or "").strip()

    default_units = ["nginx", "redis-server", "tftpd-hpa", "ipxe-manager", "ipxe-celery"]
    if args.systemd_units:
        units_list = list(args.systemd_units)
    elif args.systemd:
        units_list = default_units
    else:
        units_list = []

    audit = Audit(
        base,
        timeout=args.timeout,
        insecure=args.insecure,
        strict_menus=args.strict_menus,
        skip_menus=args.skip_menus,
        include_openapi=args.include_openapi,
    )

    print(f"Cible HTTP : {base}\n")

    run_public_phase(audit)
    run_menu_phase(audit)

    auth_ok = run_authenticated_pages(audit, pw)
    if not auth_ok:
        print("Résultat : impossible d’obtenir une session admin — code 2.", file=sys.stderr)
        return 2

    if args.check_redis and not check_redis_cli():
        audit.failures += 1

    if args.celery_inspect and not check_celery(args.app_dir, args.venv):
        audit.failures += 1

    if units_list:
        audit.failures += check_systemd_units(units_list)

    if audit.warnings:
        print(f"Avertissements : {audit.warnings} (optionnel / menu manquant).")
    if audit.failures:
        print(f"Résultat : {audit.failures} échec(s) — code 1.")
        return 1
    print("Résultat : OK — code 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())