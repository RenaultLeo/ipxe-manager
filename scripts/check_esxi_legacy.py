#!/usr/bin/env python3
"""
Contrôle ESXi Legacy (BIOS / mboot.c32) :
- versions ESXi prêtes en base
- présence des artefacts boot (kernel mboot.c32, ipxe-boot.cfg, modules JSON)
- cohérence kernelopt (runweasel)

Usage serveur :
  sudo -u ipxe /srv/ipxe/venv/bin/python /srv/ipxe/app/scripts/check_esxi_legacy.py
"""
from __future__ import annotations

import json
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


def _kernelopt_contains_runweasel(cfg_path: Path) -> bool:
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("kernelopt="):
            val = line.split("=", 1)[1].strip().lower()
            return "runweasel" in val.split() or "runweasel" in val
    return False


def main() -> int:
    errors = 0
    print("=== iPXE Manager — check ESXi Legacy ===\n")
    try:
        from app.database import SessionLocal, init_db
        from app.models.models import IsoVersion
        from app.config import settings
    except Exception as exc:
        ko(f"Imports applicatifs: {exc}")
        return 1

    try:
        init_db()
        ok("init_db OK")
    except Exception as exc:
        ko(f"init_db échoue: {exc}")
        return 1

    db = SessionLocal()
    try:
        versions = (
            db.query(IsoVersion)
            .filter(IsoVersion.status == "ready")
            .all()
        )
        esxi_versions = [
            v for v in versions
            if ((v.os_type.slug or "").lower() == "esxi" or (v.os_type.boot_type or "").lower() == "esxi")
        ]
        if not esxi_versions:
            warn("Aucune version ESXi en statut ready")
            print("\nRésultat : aucun ESXi ready à vérifier.")
            return 0

        ok(f"Versions ESXi ready: {len(esxi_versions)}")
        http_root = Path(settings.http_root)
        for v in esxi_versions:
            print(f"\n- Version #{v.id}: {v.version_label}")
            be = v.boot_entry
            if not be:
                ko("boot_entry absent")
                errors += 1
                continue

            krel = (be.kernel_path or "").strip()
            if not krel:
                ko("kernel_path vide (mboot.c32 attendu)")
                errors += 1
            else:
                kname = krel.replace("\\", "/").split("/")[-1].lower()
                if kname != "mboot.c32":
                    ko(f"kernel_path inattendu pour legacy: {krel} (mboot.c32 attendu)")
                    errors += 1
                else:
                    ok(f"kernel_path legacy: {krel}")

                kpath = http_root / krel.lstrip("/").replace("\\", "/")
                if kpath.is_file():
                    ok(f"fichier kernel présent: {kpath}")
                else:
                    ko(f"fichier kernel absent: {kpath}")
                    errors += 1

            cfg_rel = (be.esxi_boot_cfg_path or "").strip()
            if not cfg_rel:
                ko("esxi_boot_cfg_path vide")
                errors += 1
                continue
            cfg_path = http_root / cfg_rel.lstrip("/").replace("\\", "/")
            if cfg_path.is_file():
                ok(f"ipxe-boot.cfg présent: {cfg_path}")
            else:
                ko(f"ipxe-boot.cfg absent: {cfg_path}")
                errors += 1
                continue

            if _kernelopt_contains_runweasel(cfg_path):
                ok("kernelopt contient runweasel")
            else:
                ko("kernelopt sans runweasel")
                errors += 1

            mods_raw = (be.esxi_modules or "").strip()
            if not mods_raw:
                ko("esxi_modules vide")
                errors += 1
                continue
            try:
                mods = json.loads(mods_raw)
                if not isinstance(mods, list) or not mods:
                    ko("esxi_modules JSON invalide/vide")
                    errors += 1
                    continue
                ok(f"esxi_modules JSON: {len(mods)} entrée(s)")
            except Exception as exc:
                ko(f"esxi_modules JSON invalide: {exc}")
                errors += 1
                continue

            missing = 0
            for rel in mods[:50]:
                p = http_root / str(rel).lstrip("/").replace("\\", "/")
                if not p.is_file():
                    missing += 1
            if missing == 0:
                ok("modules Legacy présents (échantillon)")
            else:
                ko(f"modules manquants (échantillon): {missing}")
                errors += 1
    finally:
        db.close()

    print()
    if errors:
        print(f"Résultat : {errors} problème(s) ESXi Legacy.")
        return 1
    print("Résultat : ESXi Legacy OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

