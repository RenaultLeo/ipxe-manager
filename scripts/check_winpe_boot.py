#!/usr/bin/env python3
"""Vérifie le flux WinPE : BDD, wimboot, wimupdate, chemins boot.wim."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    errors: list[str] = []
    try:
        from sqlalchemy.orm import configure_mappers
        from app.models import models  # noqa: F401

        configure_mappers()
        from app.database import init_db, SessionLocal
        from app.models.models import IsoVersion, OsType
        from sqlalchemy.orm import joinedload
        import shutil

        init_db()
        db = SessionLocal()
        try:
            win = db.query(OsType).filter(OsType.slug.in_(("winpe", "windows"))).all()
            if not win:
                errors.append("Types OS winpe/windows absents en BDD")
            wimboot = Path(__file__).resolve().parents[1].parent / "http" / "wimboot"
            from app.config import settings

            wb = Path(settings.http_root) / "wimboot"
            if not wb.is_file():
                errors.append(f"wimboot absent : {wb}")
            if not (shutil.which("wimupdate") or shutil.which("wimlib-imagex")):
                errors.append("wimupdate / wimlib-imagex absent (apt install wimtools)")
            vers = (
                db.query(IsoVersion)
                .options(
                    joinedload(IsoVersion.os_type),
                    joinedload(IsoVersion.boot_entry),
                    joinedload(IsoVersion.winpe_installs),
                )
                .filter(IsoVersion.status == "ready")
                .all()
            )
            for v in vers:
                if (v.os_type.boot_type or "") != "windows":
                    continue
                be = v.boot_entry
                if not be:
                    continue
                for field in ("bcd_path", "boot_sdi_path", "boot_wim_path"):
                    rel = getattr(be, field, None)
                    if not rel:
                        errors.append(f"v{v.id}: {field} manquant")
                        continue
                    if not (Path(settings.http_root) / rel.replace("\\", "/")).is_file():
                        errors.append(f"v{v.id}: fichier absent pour {field} ({rel})")
        finally:
            db.close()
    except Exception as exc:
        errors.append(str(exc))

    if errors:
        print("WinPE check — ERREURS:")
        for e in errors:
            print(" -", e)
        return 1
    print("WinPE check — OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
