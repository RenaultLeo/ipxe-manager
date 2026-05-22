"""Utilitaires wimupdate / chemins boot.wim (sans dépendance vers winpe_scripts)."""
from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings
from app.models.models import IsoVersion
from app.services.iso_extractor import ExtractionError
from app.services.windows_boot_paths import resolve_http_rel

logger = logging.getLogger(__name__)

STARTNET_WIM_PATH = "Windows/System32/startnet.cmd"


def boot_wim_filesystem_path(version: IsoVersion) -> Path:
    be = version.boot_entry
    if not be or not (be.boot_wim_path or "").strip():
        raise FileNotFoundError(
            "boot.wim absent — extrayez l'ISO WinPE ou uploadez boot.wim avant de générer les scripts."
        )
    try:
        return resolve_http_rel(be.boot_wim_path)
    except FileNotFoundError:
        rel = be.boot_wim_path.replace("\\", "/").lstrip("/")
        path = Path(settings.http_root) / rel
        raise FileNotFoundError(f"boot.wim introuvable sur le disque : {path}") from None


def _wimupdate_argv(wim_file: str, image_index: int) -> list[str]:
    wu = shutil.which("wimupdate")
    if wu:
        return [wu, wim_file, str(image_index)]
    wi = shutil.which("wimlib-imagex")
    if wi:
        return [wi, "update", wim_file, str(image_index)]
    raise ExtractionError(
        "wimupdate / wimlib-imagex introuvable — apt install wimtools"
    )


def run_wimupdate_add(
    boot_wim: Path,
    image_index: int,
    src: str,
    dest_in_wim: str,
) -> subprocess.CompletedProcess[str]:
    """Exécute « add SOURCE DEST » via fichier de commandes (man wimupdate)."""
    add_line = f"add {shlex.quote(src)} {shlex.quote(dest_in_wim)}\n"
    one_cmd = f"add {shlex.quote(src)} {shlex.quote(dest_in_wim)}"
    wim_str = str(boot_wim.resolve())
    attempts: list[list[str]] = [_wimupdate_argv(wim_str, image_index)]
    wu = shutil.which("wimupdate")
    if wu:
        attempts.append([wu, wim_str])
    wi = shutil.which("wimlib-imagex")
    if wi:
        attempts.append([wi, "update", wim_str])

    last: subprocess.CompletedProcess[str] | None = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".wimupdate.txt",
        delete=False,
        encoding="utf-8",
    ) as uf:
        uf.write(add_line)
        uf.flush()
        update_cmd_path = Path(uf.name)

    try:
        for base_argv in attempts:
            with open(update_cmd_path, encoding="utf-8") as cmdf:
                last = subprocess.run(
                    base_argv,
                    stdin=cmdf,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            if last.returncode == 0:
                return last
            last = subprocess.run(
                base_argv + ["--command", one_cmd],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if last.returncode == 0:
                return last
        assert last is not None
        return last
    finally:
        try:
            update_cmd_path.unlink(missing_ok=True)
        except OSError:
            pass
