#!/usr/bin/env python3
"""
Vérifie le flux WinPE : BDD, wimboot, wimupdate, chemins boot.wim,
cohérence SMB (montage Z: dans startnet.cmd) et tâches Celery patch_winpe_startnet.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _z_path_from_rel(rel: str) -> str:
    win_rel = rel.replace("/", "\\")
    if win_rel.lower().startswith("boot\\"):
        win_rel = win_rel[5:]
    return f"Z:\\{win_rel}"


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        from sqlalchemy.orm import configure_mappers
        from app.models import models  # noqa: F401

        configure_mappers()
        from app.database import init_db, SessionLocal
        from app.models.models import IsoVersion, OsType, WinpeInstall
        from sqlalchemy.orm import joinedload
        import shutil
        import subprocess

        from app.config import settings
        from app.services.winpe_installs import (
            install_wim_path,
            install_wim_rel_path,
            installs_root,
            smb_host_from_settings,
            smb_share_name,
            smb_unc_install_wim,
        )
        from app.services.winpe_startnet import (
            boot_wim_filesystem_path,
            generate_startnet_cmd,
        )
        from app.tasks.celery_app import celery
        import app.tasks.jobs  # noqa: F401 — enregistre upload_winpe_install + patch_winpe_startnet

        init_db()
        db = SessionLocal()
        try:
            win = db.query(OsType).filter(OsType.slug.in_(("winpe", "windows"))).all()
            if not win:
                errors.append("Types OS winpe/windows absents en BDD")

            wb = Path(settings.http_root) / "wimboot"
            if not wb.is_file():
                errors.append(f"wimboot absent : {wb}")
            if not (shutil.which("wimupdate") or shutil.which("wimlib-imagex")):
                errors.append("wimupdate / wimlib-imagex absent (apt install wimtools)")

            boot_share = settings.boot_dir
            if not boot_share.is_dir():
                errors.append(f"Arbre boot HTTP absent : {boot_share}")
            else:
                print(f"Partage SMB [{smb_share_name()}] → {boot_share.resolve()}")

            task_names = {t.name for t in celery.tasks.values() if t.name}
            for required in ("upload_winpe_install", "patch_winpe_startnet"):
                if required not in task_names:
                    errors.append(f"Tâche Celery « {required} » non enregistrée")
                else:
                    print(f"Tâche Celery enregistrée : {required}")

            import os

            uid = os.getuid()
            print(f"Utilisateur effectif : uid={uid} (worker ipxe-celery = utilisateur ipxe, uid typique du compte système)")

            boot_share = settings.boot_dir
            if boot_share.is_dir():
                probe = boot_share / ".celery_write_probe"
                try:
                    probe.write_text("ok", encoding="utf-8")
                    probe.unlink(missing_ok=True)
                    print(f"Écriture OK sous {boot_share}")
                except OSError as exc:
                    errors.append(
                        f"Pas d'écriture sur {boot_share} (Celery ne pourra pas patcher boot.wim) : {exc}"
                    )

            host = smb_host_from_settings()
            print(f"Hôte SMB startnet : \\\\{host}\\{smb_share_name()}")

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

                try:
                    boot_wim_filesystem_path(v)
                except FileNotFoundError as exc:
                    errors.append(f"v{v.id}: {exc}")

                root = installs_root(v)
                installs = list(v.winpe_installs or [])
                if not installs and root.is_dir():
                    warnings.append(
                        f"v{v.id}: dossiers installs/ sur disque mais aucune entrée WinpeInstall en BDD"
                    )

                for wi in installs:
                    disk = install_wim_path(v, wi.slug)
                    rel = install_wim_rel_path(v, wi.slug)
                    z = _z_path_from_rel(rel)
                    unc = smb_unc_install_wim(v, wi)

                    if not disk.is_file():
                        errors.append(
                            f"v{v.id} install « {wi.slug} » : install.wim absent ({disk})"
                        )
                        continue

                    under_share = boot_share / Path(rel.replace("\\", "/")).relative_to("boot")
                    if not under_share.is_file():
                        errors.append(
                            f"v{v.id} install « {wi.slug} » : chemin SMB incohérent "
                            f"(attendu sous {boot_share}: {under_share})"
                        )

                    script = generate_startnet_cmd(v, wi)
                    for needle in (
                        "wpeinit",
                        "net use Z:",
                        "set WIM=",
                        "Dism /Apply-Image",
                    ):
                        if needle not in script:
                            errors.append(
                                f"v{v.id} install « {wi.slug} » : startnet.cmd sans « {needle} »"
                            )
                    if z not in script:
                        errors.append(
                            f"v{v.id} install « {wi.slug} » : WIM Z: absent du script ({z})"
                        )

                    print(
                        f"  v{v.id} [{wi.slug}] OK — disque={disk.stat().st_size // (1024**2)} MiB"
                    )
                    print(f"    UNC   {unc}")
                    print(f"    WinPE {z}")
                    print(f"    injecté dans boot.wim → Windows/System32/startnet.cmd (via wimupdate)")

                if v.active_winpe_install_id and not v.winpe_startnet_patched_at:
                    aid = v.active_winpe_install_id
                    warnings.append(
                        f"v{v.id}: active_winpe_install_id={aid} mais winpe_startnet_patched_at vide "
                        "(injection Celery non confirmée — ancienne BDD ou tâche échouée)"
                    )
                    try:
                        boot_p = boot_wim_filesystem_path(v)
                        wimdir = shutil.which("wimdir") or shutil.which("wimlib-imagex")
                        idx = int(getattr(settings, "winpe_boot_wim_index", 1) or 1)
                        if wimdir and boot_p.is_file():
                            proc = subprocess.run(
                                [wimdir, str(boot_p), str(idx), "--path"],
                                capture_output=True,
                                text=True,
                                timeout=120,
                            )
                            out = (proc.stdout or "") + (proc.stderr or "")
                            if "startnet.cmd" in out.lower():
                                print(
                                    f"  v{v.id}: startnet.cmd semble présent dans boot.wim "
                                    "(relancer l'injection pour mettre à jour la BDD, ou vérifier le contenu)"
                                )
                            else:
                                warnings.append(
                                    f"v{v.id}: startnet.cmd absent de boot.wim — "
                                    "relancer « Injecter dans boot.wim » dans l'UI"
                                )
                    except Exception as exc:
                        warnings.append(f"v{v.id}: vérif boot.wim impossible : {exc}")
                    print(
                        f"  → Relancer : sudo -u ipxe bash -c 'cd /srv/ipxe/app && "
                        f"/srv/ipxe/venv/bin/celery -A app.tasks.celery_app call patch_winpe_startnet "
                        f"--args=\"[{v.id}, {aid}]\"'"
                    )
        finally:
            db.close()
    except Exception as exc:
        errors.append(str(exc))

    if warnings:
        print("WinPE check — avertissements:")
        for w in warnings:
            print(" !", w)

    if errors:
        print("WinPE check — ERREURS:")
        for e in errors:
            print(" -", e)
        return 1
    print("WinPE check — OK (chemins SMB / Celery / wimupdate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
