#!/usr/bin/env python3
"""
Contrôle rapide du pipeline d'extraction ISO (outils, chemins, droits, Celery).
Usage sur le serveur : sudo -u ipxe /srv/ipxe/venv/bin/python /srv/ipxe/app/scripts/check_extraction.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def warn(msg: str) -> None:
    print(f"  !!  {msg}")


def ko(msg: str) -> None:
    print(f"  KO  {msg}")


def main() -> int:
    errors = 0
    print("=== iPXE Manager — check extraction ===\n")

    tools = [("xorriso", "xorriso"), ("7z", "7z"), ("7za", "7za"), ("bsdtar", "bsdtar")]
    found = [name for name, cmd in tools if shutil.which(cmd)]
    if found:
        ok(f"Outils ISO : {', '.join(found)}")
    else:
        ko("Aucun outil d'extraction (xorriso / 7z / bsdtar)")
        errors += 1

    try:
        from app.config import settings

        boot = Path(settings.boot_dir)
        menus = Path(settings.menus_dir)
        iso_root = Path(settings.iso_root)
        for label, p in (("boot", boot), ("menus", menus), ("isos", iso_root)):
            if not p.is_dir():
                ko(f"{label} absent : {p}")
                errors += 1
                continue
            if os.access(p, os.W_OK | os.X_OK):
                ok(f"{label} inscriptible : {p}")
            else:
                ko(f"{label} non inscriptible pour cet utilisateur : {p}")
                errors += 1
    except Exception as exc:
        ko(f"Config / chemins : {exc}")
        errors += 1
        return 1

    menu_ipxe = menus / "menu.ipxe"
    if menu_ipxe.is_file():
        if os.access(menu_ipxe, os.W_OK):
            ok(f"menu.ipxe modifiable : {menu_ipxe}")
        else:
            warn(
                f"menu.ipxe non modifiable (Celery échouera) : {menu_ipxe} "
                f"— sudo bash deploy/fix-menus-permissions.sh"
            )
            errors += 1
    else:
        warn(f"menu.ipxe absent (normal avant 1re régénération) : {menu_ipxe}")

    try:
        import redis

        r = redis.from_url(settings.redis_url)
        r.ping()
        ok(f"Redis : {settings.redis_url}")
    except Exception as exc:
        warn(f"Redis : {exc}")

    try:
        from app.tasks.celery_app import celery

        insp = celery.control.inspect(timeout=3.0)
        ping = insp.ping() if insp else None
        if ping:
            ok(f"Celery worker(s) : {', '.join(ping.keys())}")
        else:
            warn("Aucun worker Celery joignable (ipxe-celery démarré ?)")
    except Exception as exc:
        warn(f"Celery inspect : {exc}")

    try:
        from app.database import SessionLocal, init_db
        from app.models.models import IsoVersion, Upload

        init_db()
        db = SessionLocal()
        try:
            stuck = (
                db.query(IsoVersion).filter(IsoVersion.status == "extracting").count()
            )
            if stuck:
                warn(f"{stuck} version(s) bloquée(s) en « extracting »")
            else:
                ok("Aucune version bloquée en extracting")

            err_up = (
                db.query(Upload)
                .filter(
                    Upload.file_type == "extraction",
                    Upload.status == "error",
                )
                .count()
            )
            if err_up:
                warn(f"{err_up} upload(s) extraction en erreur (voir fiche ISO)")
            else:
                ok("Aucun upload extraction en erreur récent (global)")
        finally:
            db.close()
    except Exception as exc:
        warn(f"Base SQLite : {exc}")

    print()
    if errors:
        print(f"Résultat : {errors} problème(s) à corriger.")
        return 1
    print("Résultat : extraction prête (outils + chemins + menus).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
