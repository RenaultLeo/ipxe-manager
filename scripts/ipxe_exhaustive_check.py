#!/usr/bin/env python3
"""
Audit HTTP étendu pour iPXE Manager (stdlib uniquement).

- Pages publiques : /login, redirections racine, statiques, /set-language, POST login invalide.
- Menus Nginx /menus/menu.ipxe : statut HTTP + validates iPXE (#!ipxe, directives menu/item).
- Avec mot de passe admin : cookie de session, puis toutes les pages UI principales (200 HTML),
  API JSON léger (/isos/upload/precheck), image Paramètres, etc.
- Optionnel : systemd (Linux), redis-cli ping, celery inspect ping ; avec **--full-local** : DB SQLite,
  arborescence disque depuis **.env**, Samba (**smbd**/ **nmbd**, port **445**, **smbclient** si installé),
  exports NFS (**exportfs**), ports **6379**/ **2049**, sondes HTTP complémentaires (thème menu, **wimboot**).

Exemple :

  python3 scripts/ipxe_exhaustive_check.py --base-url http://192.168.2.6 --password VOTRE_MDP

  # Sur la machine réelle iPXE (SSH) — tout le périmètre local + systemd + Redis/Celery
  python3 scripts/ipxe_exhaustive_check.py --base-url http://127.0.0.1 --password "$IPXE_ADMIN_PW" \\
      --full-local --systemd --strict-menus --check-redis --celery-inspect

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
import socket
import sqlite3
import ssl
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlencode, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.os_type_seed import (  # noqa: E402
    EXPECTED_BUILTIN_OS_SLUGS,
    validate_builtin_os_slugs,
)

USER_AGENT = "ipxe-exhaustive-check/1.0"

LOGIN_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# Tables attendues pour alignement avec app/models/models.py (+ alembic éventuelle)
_EXPECTED_SQLITE_TABLES = frozenset({
    "users",
    "os_types",
    "iso_versions",
    "boot_entries",
    "winpe_installs",
    "autoconfigs",
    "uploads",
    "app_settings",
    "remote_chains",
})


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


def _cookie_from_set_cookie_header(hdrs: dict[str, str]) -> str:
    """Premier cookie « name=value » renvoyé par Set-Cookie (session vidée après logout)."""
    sc = hdrs.get("set-cookie", "")
    if not sc:
        return ""
    return sc.split(";", 1)[0].strip()


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
        self._category = "Général"
        self.category_order: list[str] = []
        self.fail_by_cat: dict[str, int] = {}
        self.warn_by_cat: dict[str, int] = {}
        self.checks_ok_by_cat: dict[str, int] = {}

    def set_category(self, name: str) -> None:
        """Réassigne la catégorie courante pour les prochains ok()/warn() et conserve l’ordre d’affichage du bilan."""
        c = name.strip() or "Général"
        self._category = c
        if c not in self.category_order:
            self.category_order.append(c)

    def _bump_failure(self) -> None:
        self.failures += 1
        c = self._category
        self.fail_by_cat[c] = self.fail_by_cat.get(c, 0) + 1

    def _bump_warning(self) -> None:
        self.warnings += 1
        c = self._category
        self.warn_by_cat[c] = self.warn_by_cat.get(c, 0) + 1

    def _bump_success(self) -> None:
        """Compte un contrôle explicite réussi — pour synthèse bilan."""
        c = self._category
        self.checks_ok_by_cat[c] = self.checks_ok_by_cat.get(c, 0) + 1

    def ok(self, cond: bool, msg: str) -> None:
        print(f"  {'✓' if cond else '✗'} {msg}")
        if cond:
            self._bump_success()
        else:
            self._bump_failure()

    def warn(self, cond: bool, msg: str) -> None:
        print(f"  {'✓' if cond else '⚠'} {msg}")
        if not cond:
            self._bump_warning()

    def _hdr(self, with_cookie: bool) -> dict[str, str]:
        if with_cookie and self.cookie:
            return {"Cookie": self.cookie}
        return {}

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


def print_audit_recap_by_category(audit: Audit) -> None:
    """Synthèse finale : statut, nombre d’échecs et d’avertissements par catégorie parcourue."""
    if not audit.category_order:
        return
    print(
        "\n"
        "══════════════════════════════════════════════════════════\n"
        "  Bilan par catégorie\n"
        "══════════════════════════════════════════════════════════"
    )
    for cat in audit.category_order:
        n_ok = audit.checks_ok_by_cat.get(cat, 0)
        nf = audit.fail_by_cat.get(cat, 0)
        nw = audit.warn_by_cat.get(cat, 0)
        if nf > 0:
            etat = "KO"
        elif nw > 0:
            etat = "OK (avertissements)"
        else:
            etat = "OK"
        print(f"\n▸ {cat}")
        print(f"    État : {etat}")
        print(f"    Contrôles réussis (✓ explicites) : {n_ok}")
        print(f"    Échecs (✗) : {nf}")
        print(f"    Avertissements (⚠) : {nw}")
    print(
        "\n"
        "──────────────────────────────────────────────────────────\n"
        f"  Totaux globaux — échecs : {audit.failures} · "
        f"avertissements : {audit.warnings}\n"
        "══════════════════════════════════════════════════════════\n"
    )


def run_public_phase(audit: Audit) -> None:
    audit.set_category("HTTP — Public (sans session)")
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
    audit.set_category("HTTP — Menus iPXE (fichiers Nginx)")
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
            audit.ok(True, "Structure menu.ipxe — #!ipxe + directives menu/item")
        else:
            audit.ok(False, f"Menu mal formé ou incomplet : {detail}")
    print()


def run_authenticated_pages(audit: Audit, password: str, session_cookie: str = "") -> bool:
    """Établit la session et vérifie les pages. Retourne False si login impossible."""
    cookie_in = (session_cookie or "").strip()
    if cookie_in:
        print("=== Session admin (cookie fourni — interface Supervision) ===")
        audit.set_category("HTTP — Session admin")
        audit.cookie = cookie_in if "=" in cookie_in else f"session={cookie_in}"
        code_sess, _, body_sess = http_request(
            audit.base,
            "/admin/supervision",
            timeout=audit.timeout,
            insecure=audit.insecure,
            headers=audit._hdr(True),
        )
        sess_ok = code_sess == 200 and b"<html" in body_sess.lower()
        if not sess_ok:
            audit.ok(
                False,
                f"Cookie session invalide ou expiré → GET /admin/supervision HTTP {code_sess} "
                "(reconnectez-vous à l’UI puis relancez l’audit)",
            )
            print()
            return False
        audit.ok(True, "Cookie de session navigateur valide (GET /admin/supervision → 200)")
    elif not password:
        print(
            "=== Pages authentifiées — saut (fournissez --password, --session-cookie ou la variable "
            "d’environnement IPXE_AUDIT_PASSWORD) ===\n"
        )
        return True
    else:
        print("=== Session admin (pages HTML / JSON légers) ===")
        audit.set_category("HTTP — Session admin")
        status, ck = post_login_cookie(audit.base, password, audit.timeout, audit.insecure)
        if status != 302 or not ck:
            audit.ok(False, f"Connexion POST /login : statut={status}, cookie défini={'oui' if ck else 'non'}")
            print()
            return False

        audit.ok(True, f"POST /login → {status}, cookie session reçu")
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
        ("/admin/supervision", "supervision admin"),
        ("/admin/users", "comptes utilisateurs"),
    )
    for path, title in html_paths:
        audit.check_get(path, (200,), auth=True, name=f"HTML — {title} [{path}]", substring=b"<html")

    code_snap, _, snap_body = http_request(
        audit.base,
        "/admin/supervision/api/snapshot",
        timeout=max(audit.timeout, 45.0),
        insecure=audit.insecure,
        headers={
            **audit._hdr(True),
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    snap_ok = False
    snap_hint = f"HTTP {code_snap}"
    if code_snap == 200:
        try:
            payload = json.loads(snap_body.decode("utf-8"))
            snap_ok = isinstance(payload, dict) and "services" in payload and "host" in payload
            snap_hint = "JSON snapshot valide"
        except json.JSONDecodeError:
            snap_hint = "corps non JSON"
    audit.ok(snap_ok, f"GET /admin/supervision/api/snapshot → {snap_hint}")

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
        headers={
            **audit._hdr(True),
            "X-Requested-With": "XMLHttpRequest",
        },
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
        headers={
            **audit._hdr(True),
            "X-Requested-With": "XMLHttpRequest",
        },
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
    # Comme le navigateur : ne plus réutiliser l’ancien cookie signé (sinon session encore valide)
    audit.cookie = _cookie_from_set_cookie_header(hdrs_lo)

    code_gone, hdrs_gone, _ = http_request(
        audit.base,
        "/settings",
        timeout=audit.timeout,
        insecure=audit.insecure,
        headers=audit._hdr(bool(audit.cookie)),
    )
    loc_gone = hdrs_gone.get("location", "") or ""
    audit.ok(
        code_gone in LOGIN_REDIRECT_CODES and "login" in loc_gone.lower(),
        f"après logout, GET /settings → {code_gone} Location={loc_gone!r}",
    )

    print()
    audit.cookie = ""
    return True


def load_env_map(app_dir: str) -> dict[str, str]:
    path = os.path.join(app_dir.strip().rstrip("/\\"), ".env")
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
    except OSError:
        return env
    for ln in raw_lines:
        line = ln.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def sqlite_filesystem_path(database_url: str) -> str | None:
    u = database_url.strip()
    if not u.lower().startswith("sqlite:///"):
        return None
    tail = u[len("sqlite:///") :].strip().split("?", 1)[0].split("#", 1)[0].strip()
    return tail.replace("\\", "/") if tail else None


def check_database_application(database_url: str, audit: Audit) -> None:
    audit.set_category("Base de données (.env / SQLite ou Postgres)")
    print("=== Base de données (intégrité + tables + seed minimal) ===")
    spath = sqlite_filesystem_path(database_url)
    du_low = database_url.strip().lower()
    if du_low.startswith("postgresql") or du_low.startswith("postgres"):
        try:
            out = subprocess.run(
                ["pg_isready"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except FileNotFoundError:
            audit.warn(False, "PostgreSQL (DATABASE_URL) — pg_isready absent ; vérifie manuellement le serveur SQL")
            print()
            return
        merged = ((out.stdout or "") + (out.stderr or "")).strip()
        ok = out.returncode == 0
        audit.ok(ok, f"pg_isready → RC={out.returncode} ({merged[:140]})")
        print()
        return

    if not spath:
        audit.warn(False, f"DATABASE_URL non analysable comme SQLite automatiquement ({database_url[:72]}…)")
        print()
        return

    if not os.path.isfile(spath):
        audit.ok(False, f"fichier SQLite introuvable : {spath}")
        print()
        return

    integrity_issue: str | None = None
    missing_tables: list[str] = []
    fk_errors: int | None = None
    ot_count: int | None = None
    ot_slugs: list[str] = []
    missing_seed: list[str] = []
    legacy_slugs: list[str] = []

    try:
        conn = sqlite3.connect(f"file:{spath}?mode=ro", uri=True, timeout=20.0)
    except sqlite3.Error as ex:
        audit.ok(False, f"SQLite read-only impossible : {ex}")
        print()
        return

    try:
        row_ic = conn.execute("PRAGMA integrity_check").fetchone()
        if not row_ic or str(row_ic[0]).lower() != "ok":
            integrity_issue = str(row_ic[0]) if row_ic else "sans résultat"

        present = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        missing_tables = sorted(_EXPECTED_SQLITE_TABLES - present)

        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        fk_errors = len(fk_rows)

        ott = conn.execute("SELECT COUNT(*) FROM os_types").fetchone()
        ot_count = int(ott[0]) if ott else 0
        ot_slugs = [str(r[0]) for r in conn.execute("SELECT slug FROM os_types").fetchall()]
        missing_seed, legacy_slugs = validate_builtin_os_slugs(ot_slugs)
    except sqlite3.Error as ex:
        integrity_issue = str(ex)
    finally:
        conn.close()

    if integrity_issue:
        audit.ok(False, f"SQLite : {integrity_issue}")
    else:
        audit.ok(True, "SQLite PRAGMA integrity_check → ok")

    if integrity_issue is None:
        if missing_tables:
            audit.ok(False, f"tables attendues absentes {missing_tables} (cf. app/models/models.py)")
        else:
            audit.ok(True, f"tables SQL métier {_EXPECTED_SQLITE_TABLES}")

        if fk_errors is not None:
            audit.ok(fk_errors == 0, f"SQLite foreign_key_check → {fk_errors} violation(s)")

        if ot_count is not None:
            audit.ok(
                ot_count >= len(EXPECTED_BUILTIN_OS_SLUGS),
                f"os_types.COUNT={ot_count} — attendu ≥{len(EXPECTED_BUILTIN_OS_SLUGS)} "
                f"(seed deploy/seed_db.py)",
            )
            audit.ok(
                not missing_seed,
                f"slugs seed intégrés — manquants : {missing_seed or 'aucun'}",
            )
            audit.ok(
                not legacy_slugs,
                f"slug legacy « winpe » encore présent ({legacy_slugs}) — "
                f"lancer init_db() (WinPE = mode Windows)",
            )

    try:
        sz = os.path.getsize(spath)
        print(f"  (fichier {spath}, {sz} octets)\n")
    except OSError:
        print()


def check_filesystem_layout(env_map: dict[str, str], audit: Audit) -> None:
    audit.set_category("Disque / arborescence (.env)")
    print("=== Disque (valeurs dans .env) ===")
    for key in ("TFTP_ROOT", "HTTP_ROOT", "ISO_ROOT", "BUILD_DIR"):
        pth = (env_map.get(key) or "").strip()
        if not pth:
            audit.ok(False, f"{key} vide ou absent dans .env")
            continue
        audit.ok(os.path.isdir(pth), f"{key} est un dossier → {pth}")

    iso_alias = (env_map.get("ISO_HTTP_ALIAS") or "isos-ipxe").strip().strip("/")
    print(f"  (prévu Nginx ISO → /{iso_alias}/)")

    http_root = env_map.get("HTTP_ROOT", "").strip().rstrip("/")
    tft = env_map.get("TFTP_ROOT", "").strip().rstrip("/")
    iso_root_p = env_map.get("ISO_ROOT", "").strip().rstrip("/")

    if http_root:
        menus = os.path.join(http_root, "menus")
        boot_local = os.path.join(http_root, "boot")
        bundle_menu = os.path.join(http_root, "menus", "menu.ipxe")
        audit.ok(os.path.isdir(menus), f"dossier {menus}")
        audit.ok(os.path.isdir(boot_local), f"dossier {boot_local}")
        configs_d = os.path.join(http_root, "configs")
        audit.ok(os.path.isdir(configs_d), f"dossier {configs_d}")
        if os.path.isfile(bundle_menu):
            print(f"  ✓ {bundle_menu}")
        else:
            audit.warn(False, f"menus/menu.ipxe absent sur disque ({bundle_menu})")

    if tft and os.path.isdir(tft):
        undi = os.path.join(tft, "undionly.kpxe")
        audit.ok(os.path.isfile(undi), f"TFTP undionly.kpxe conseillé → {undi}")

    if iso_root_p and os.path.isdir(iso_root_p):
        print(f"  ✓ Samba « isos » / HTTP /{iso_alias}/ pointent ISO_ROOT ({iso_root_p})")

    print()


def tcp_port_open(bind: str, port: int, timeout_s: float = 3.0) -> bool:
    try:
        with socket.create_connection((bind, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def udp_tftp_responsive(bind_host: str, timeout_s: float = 2.0) -> bool:
    """Une requête RRQ invalide doit recevoir au moins un paquet d’erreur si tftpd répond."""
    sk: socket.socket | None = None
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.settimeout(timeout_s)
        sk.sendto(b"\x00\x01t\x00netascii\x00", (bind_host, 69))
        _d, _a = sk.recvfrom(2048)
        return True
    except OSError:
        return False
    finally:
        if sk:
            try:
                sk.close()
            except OSError:
                pass


def check_local_listen_services(audit: Audit) -> None:
    audit.set_category("Ports localhost (SMB · Redis · TFTP)")
    print("=== Ports critiques (localhost — exécute ce script SUR le serveur iPXE) ===")
    audit.ok(
        tcp_port_open("127.0.0.1", 445),
        "SMB smbd / Microsoft-DS : TCP :445 doit répondre sur 127.0.0.1",
    )
    audit.ok(tcp_port_open("127.0.0.1", 6379), "Redis : TCP :6379 sur 127.0.0.1")

    nfs_listen = tcp_port_open("127.0.0.1", 2049)
    if nfs_listen:
        print("  ✓ NFS nfsd tcp :2049 répond")
    else:
        print(
            "  ⚠ NFS :2049 inactif sur localhost — normal si aucun nfsroot Ubuntu ; "
            "sinon vérif nfs-kernel-server / exportfs"
        )

    u_ok = udp_tftp_responsive("127.0.0.1")
    audit.ok(u_ok, "TFTP tftpd : UDP :69 doit renvoyer un paquet depuis 127.0.0.1")

    print()


def check_smb_named_shares(audit: Audit) -> None:
    audit.set_category("Samba — partages (smbclient)")
    print("=== Samba : smbclient liste les partages [boot] [isos] ===")
    try:
        out = subprocess.run(
            ["smbclient", "-g", "-L", "localhost", "-N"],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except FileNotFoundError:
        audit.warn(False, "smbclient absent — vérif SMB depuis un client Windows ou apt install smbclient")
        print()
        return

    merged = ((out.stdout or "") + (out.stderr or "")).strip()
    ok_rc = out.returncode == 0
    audit.ok(ok_rc, f"smbclient -g -L localhost -N → RC={out.returncode}")
    lowered = "|" + merged.replace("\r\n", "\n").lower().replace("|", "|") + "|"
    boot_ok = "|boot|" in lowered
    iso_ok = "|isos|" in lowered
    audit.ok(boot_ok, "partage SMB « boot » présent dans la liste smbclient (-g)")
    audit.ok(iso_ok, "partage SMB « isos » présent dans la liste smbclient (-g)")
    print()


def check_exportfs_ubuntu_hints(audit: Audit) -> None:
    audit.set_category("NFS — exports (exportfs)")
    print("=== NFS (exportfs -v ; Ubuntu réseau) ===")
    try:
        out = subprocess.run(["exportfs", "-v"], capture_output=True, text=True, timeout=25)
    except FileNotFoundError:
        audit.warn(False, "exportfs introuvable (nfs-common / nfs-kernel-server)")
        print()
        return

    merged = ((out.stdout or "") + (out.stderr or "")).strip()
    if out.returncode != 0:
        audit.warn(False, f"exportfs -v RC={out.returncode} — exécute en root ({merged[:240]})")
        print()
        return

    slash = merged.replace("\\", "/")
    if "/srv/ipxe/http/boot/ubuntu" in slash or "/boot/ubuntu" in slash:
        audit.ok(True, "export NFS contenant boot/ubuntu (Ubuntu netboot) détecté")
    elif "/srv/ipxe" in slash:
        audit.warn(False, "exports NFS présents mais pas bootstrap Ubuntu explicite — contrôle NFS")
    else:
        audit.warn(False, "aucune export /srv/ipxe — normal si tu n’utilises pas Ubuntu+nfsroot")
    print()


def probe_static_http_aliases(audit: Audit) -> None:
    """Fichiers servis souvent uniquement par Nginx."""
    audit.set_category("HTTP — Nginx statique (thème / wimboot)")
    print("=== Alias HTTP facultatifs (thème menu, wimboot) ===")
    for uripath in ("/menus/menu-theme.png", "/wimboot"):
        code, _, _body = http_request(audit.base, uripath, timeout=audit.timeout, insecure=audit.insecure)
        audit.warn(code == 200, f"{uripath} → HTTP {code} (404 optionnel jusqu’à la génération / setup)")
    print()


def check_testparm_loaded(audit: Audit) -> None:
    audit.set_category("Samba — testparm (smb.conf)")
    print("=== Samba : testparm -s ===")
    try:
        out = subprocess.run(
            ["testparm", "-s"],
            capture_output=True,
            text=True,
            timeout=22,
        )
    except FileNotFoundError:
        audit.warn(False, "testparm absent (paquet samba-common-bin manquant)")
        print()
        return
    merged = ((out.stderr or "") + (out.stdout or "")).strip()
    audit.ok(out.returncode == 0, f"testparm -s RC={out.returncode}")
    lines = merged.splitlines()[:5]
    for ln in lines:
        print(f"  {ln}")
    print()


def run_full_local_audits(app_dir: str, audit: Audit, *, flags: argparse.Namespace) -> None:
    """
    Contrôles exécutés sur la machine où lance ce script (--full-local ou options détaillées).
    Réutilise le même objet Audit pour agréger les échecs.
    """
    env_map = load_env_map(app_dir)
    du = env_map.get("DATABASE_URL", "").strip()

    if flags.check_db:
        if du:
            check_database_application(du, audit)
        else:
            audit.set_category("Base de données (.env / SQLite ou Postgres)")
            audit.ok(False, "DATABASE_URL introuvable dans .env — impossible audit DB")

    if flags.check_fs:
        check_filesystem_layout(env_map if env_map else load_env_map(app_dir), audit)

    if flags.check_listen_ports:
        check_local_listen_services(audit)

    if flags.check_smb_shares:
        check_smb_named_shares(audit)

    if flags.check_nfs_export:
        check_exportfs_ubuntu_hints(audit)

    if flags.probe_static_http:
        probe_static_http_aliases(audit)

    if flags.check_testparm:
        check_testparm_loaded(audit)


def check_redis_cli(audit: Audit) -> None:
    audit.set_category("Redis (redis-cli)")
    print("=== Redis (redis-cli ping) ===")
    try:
        out = subprocess.run(
            ["redis-cli", "ping"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        audit.warn(False, "redis-cli absent (paquet redis-tools)")
        print()
        return
    ok = out.returncode == 0 and "PONG" in (out.stdout or "").upper()
    detail = (out.stdout or out.stderr or "").strip() or "redis-cli"
    audit.ok(ok, f"redis-cli ping → {detail}")
    print()


def check_celery_inspect(audit: Audit, app_dir: str, venv: str) -> None:
    audit.set_category("Celery (inspect ping)")
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
        audit.ok(False, f"binaire Celery introuvable : {celery_bin}")
        print()
        return
    txt = (out.stdout or "") + (out.stderr or "")
    ok = out.returncode == 0 and "pong" in txt.lower()
    audit.ok(ok, f"Celery inspect ping → exit={out.returncode}")
    if txt.strip():
        for line in txt.strip().splitlines()[:12]:
            print(f"  {line}")
        if len(txt.splitlines()) > 12:
            print("  …")
    print()


def check_systemd_units(audit: Audit, units: list[str]) -> None:
    audit.set_category("systemd (is-active)")
    print("=== systemd (is-active) ===")
    if platform.system() != "Linux":
        audit.warn(False, "plateforme non Linux — aucun contrôle systemd")
        print()
        return
    try:
        subprocess.run(["systemctl", "--version"], capture_output=True, timeout=3)
    except (FileNotFoundError, OSError):
        audit.warn(False, "systemctl introuvable")
        print()
        return
    for unit in units:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                capture_output=True,
                timeout=8,
            )
        except OSError:
            audit.ok(False, f"{unit} (erreur d’exécution systemctl)")
            continue
        ok_u = r.returncode == 0
        audit.ok(ok_u, unit)
    print()


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
        help="HTTPS : ne pas valider le certificat TLS (auto activé si --base-url est https)",
    )
    p.add_argument(
        "--strict-tls",
        action="store_true",
        help="HTTPS : exiger un certificat TLS valide (désactive l’acceptation auto-signé par défaut)",
    )
    p.add_argument("--password", default="", help="Mot de passe admin (sinon env IPXE_AUDIT_PASSWORD)")
    p.add_argument(
        "--session-cookie",
        default="",
        help="Cookie session Starlette (ex. session=…) — alternative à --password (Supervision UI)",
    )
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
        help="Unité systemd (répétable). Utilisée seule : seules ces unités sont testées ; avec --systemd sans --systemd-unit : nginx, redis-server, tftpd-hpa, ipxe-manager, ipxe-celery (+ smbd,nmbd si --full-local)",
    )
    p.add_argument(
        "--full-local",
        action="store_true",
        help="Active tous les audits locaux (voir --check-db, --check-fs, …) — à exécuter sur le SERVEUR iPXE.",
    )
    p.add_argument(
        "--check-db",
        action="store_true",
        help=".env DATABASE_URL → SQLite integrity + tables métier (+ pg_isready si Postgres)",
    )
    p.add_argument(
        "--check-fs",
        action="store_true",
        help="Chemins TFTP_ROOT, HTTP_ROOT, ISO_ROOT, BUILD_DIR depuis .env + sous-dossiers menus/boot/configs",
    )
    p.add_argument(
        "--check-listen",
        action="store_true",
        dest="check_listen_ports",
        help="TCP localhost :445 smb, :6379 redis ; UDP :69 TFTP (+ info :2049 NFS)",
    )
    p.add_argument(
        "--check-smb-shares",
        action="store_true",
        help="smbclient -L localhost -N doit lister les partages disk boot et isos",
    )
    p.add_argument(
        "--check-nfs-export",
        action="store_true",
        help="exportfs -v doit mentionner Ubuntu netboot sous /srv/ipxe/… (informationnel)",
    )
    p.add_argument(
        "--probe-static-http",
        action="store_true",
        help="HTTP supplémentaire : /menus/menu-theme.png et /wimboot via --base-url",
    )
    p.add_argument("--check-testparm", action="store_true", help="Samba testparm -s OK")
    args = p.parse_args()

    if args.full_local:
        args.check_db = True
        args.check_fs = True
        args.check_listen_ports = True
        args.check_smb_shares = True
        args.check_nfs_export = True
        args.probe_static_http = True
        args.check_testparm = True

    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        print("Erreur : --base-url invalide (ex. http://192.168.2.6)", file=sys.stderr)
        return 2

    pw = (args.password or os.environ.get("IPXE_AUDIT_PASSWORD") or "").strip()
    session_ck = (args.session_cookie or os.environ.get("IPXE_AUDIT_SESSION_COOKIE") or "").strip()

    default_units = ["nginx", "redis-server", "tftpd-hpa", "ipxe-manager", "ipxe-celery"]
    extended_units = default_units + ["smbd", "nmbd"]
    if args.systemd_units:
        units_list = list(args.systemd_units)
    elif args.systemd:
        units_list = extended_units if args.full_local else default_units
    else:
        units_list = []

    any_local_audit = bool(
        args.full_local
        or args.check_db
        or args.check_fs
        or args.check_listen_ports
        or args.check_smb_shares
        or args.check_nfs_export
        or args.probe_static_http
        or args.check_testparm
    )
    bh = (urlparse(base).hostname or "").lower()
    if any_local_audit and bh not in ("127.0.0.1", "localhost", "::1"):
        print(
            "\n⚠ Une partie des contrôles (ports 127.0.0.1, sqlite path, smbclient, exportfs) "
            "s’exécute sur CETTE machine, pas nécessairement sur l’hôte de --base-url.\n"
            "Pour un diagnostic complet, SSH sur le serveur iPXE et relance avec la même ligne.\n",
            file=sys.stderr,
        )

    use_insecure = bool(args.insecure) or (
        parsed.scheme == "https" and not args.strict_tls
    )
    if parsed.scheme == "https" and use_insecure and not args.insecure:
        print(
            "Note : HTTPS — vérification TLS assouplie (certificat auto-signé / interne).\n"
            "       Utilisez --strict-tls pour exiger une CA valide.\n",
            file=sys.stderr,
        )

    audit = Audit(
        base,
        timeout=args.timeout,
        insecure=use_insecure,
        strict_menus=args.strict_menus,
        skip_menus=args.skip_menus,
        include_openapi=args.include_openapi,
    )

    print(f"Cible HTTP : {base}" + (" (TLS assoupli)" if use_insecure else "") + "\n")

    run_public_phase(audit)
    run_menu_phase(audit)

    if any_local_audit:
        print("────────────────────────────────────────\nContrôles locaux (--check-* / --full-local)")
        run_full_local_audits(args.app_dir, audit, flags=args)

    auth_ok = run_authenticated_pages(audit, pw, session_cookie=session_ck)
    if not auth_ok:
        print_audit_recap_by_category(audit)
        print("Résultat : impossible d’obtenir une session admin — code 2.", file=sys.stderr)
        return 2

    if args.check_redis:
        check_redis_cli(audit)

    if args.celery_inspect:
        check_celery_inspect(audit, args.app_dir, args.venv)

    if units_list:
        check_systemd_units(audit, units_list)

    print_audit_recap_by_category(audit)

    if audit.warnings and audit.failures == 0:
        print(f"Note : {audit.warnings} avertissement(s) global(aux) (détails par catégorie ci‑dessus).")
    if audit.failures:
        print(f"Résultat : {audit.failures} échec(s) — code 1.")
        return 1
    print("Résultat : OK — code 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())