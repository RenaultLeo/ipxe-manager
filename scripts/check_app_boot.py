#!/usr/bin/env python3
"""Vérifie que l'app démarre (mapper SQLAlchemy + import main). Usage: python scripts/check_app_boot.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from sqlalchemy.orm import configure_mappers
        from app.models import models  # noqa: F401

        configure_mappers()
        print("SQLAlchemy mappers: OK")
        from app.database import init_db

        init_db()
        print("init_db: OK")
        from app.main import app

        print("FastAPI app:", app.title)
        delete_ok = False
        for r in app.routes:
            if getattr(r, "path", "") != "/isos/{version_id}/delete":
                continue
            ep = getattr(r, "endpoint", None)
            name = getattr(ep, "__name__", "")
            if name == "delete_iso":
                delete_ok = True
            else:
                print(f"ERREUR: /isos/{{version_id}}/delete → {name!r} (attendu delete_iso)")
                return 1
        if not delete_ok:
            print("ERREUR: route /isos/{version_id}/delete absente")
            return 1
        print("Route delete ISO: OK")
        return 0
    except Exception as exc:
        print("ERREUR:", exc, file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
