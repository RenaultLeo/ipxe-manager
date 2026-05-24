"""
Compilation firmware iPXE — clone/pull, patch general.h (HTTPS), make, copie TFTP.
Utilisé par Celery (compile_ipxe_task) et deploy/compile_ipxe_firmware.py (setup).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, list[str], list[str]], None]


def _noop_progress(step: str, completed: list[str], logs: list[str]) -> None:
    pass


def patch_ipxe_graphical_console_headers(src_dir: Path, logs: list[str]) -> None:
    """Retire les #undef qui coupent console / couleur / framebuffer en build BIOS."""
    general = src_dir / "src" / "config" / "general.h"
    console = src_dir / "src" / "config" / "console.h"
    if not general.is_file() or not console.is_file():
        raise RuntimeError(
            f"Sources iPXE incomplètes (config manquante) : {general=} {console=}"
        )

    g = general.read_text(encoding="utf-8", errors="replace")
    g_new = re.sub(r"^[ \t]*#undef CONSOLE_CMD[ \t]*\r?\n", "", g, flags=re.MULTILINE)
    if g_new != g:
        general.write_text(g_new, encoding="utf-8")
        logs.append(
            "config/general.h : retrait de #undef CONSOLE_CMD (console / colour / cpair en BIOS)."
        )

    c = console.read_text(encoding="utf-8", errors="replace")
    c_new = re.sub(
        r"^[ \t]*#undef CONSOLE_FRAMEBUFFER[ \t]*\r?\n", "", c, flags=re.MULTILINE
    )
    if c_new != c:
        console.write_text(c_new, encoding="utf-8")
        logs.append(
            "config/console.h : retrait de #undef CONSOLE_FRAMEBUFFER (fond PNG / mode graphique)."
        )

    if g_new == g and c_new == c:
        logs.append(
            "Aucun #undef CONSOLE_CMD / CONSOLE_FRAMEBUFFER trouvé (déjà patché ou sources inattendues)."
        )


def patch_ipxe_https_support(src_dir: Path, logs: list[str]) -> None:
    """Active DOWNLOAD_PROTO_HTTPS dans config/general.h (ré-appliqué après chaque git pull)."""
    general = src_dir / "src" / "config" / "general.h"
    if not general.is_file():
        raise RuntimeError(f"Sources iPXE incomplètes : {general}")

    g = general.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE):
        logs.append("config/general.h : DOWNLOAD_PROTO_HTTPS déjà activé.")
        return

    g_new, n = re.subn(
        r"^[ \t]*#undef[ \t]+DOWNLOAD_PROTO_HTTPS[^\n]*\n",
        "#define DOWNLOAD_PROTO_HTTPS\t\t/* Secure Hypertext Transfer Protocol */\n",
        g,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise RuntimeError(
            "config/general.h : ligne #undef DOWNLOAD_PROTO_HTTPS introuvable — "
            "sources iPXE inattendues."
        )
    general.write_text(g_new, encoding="utf-8")
    logs.append(
        "config/general.h : #undef DOWNLOAD_PROTO_HTTPS → #define DOWNLOAD_PROTO_HTTPS."
    )


def ipxe_tls_make_args() -> list[str]:
    """CERT= et TRUST= pour make (derniers arguments de la ligne make)."""
    ca = settings.tls_ca_cert_path
    if not ca.is_file():
        return []
    ca_s = str(ca.resolve())
    return [f"CERT={ca_s}", f"TRUST={ca_s}"]


def build_embed_ipxe(menu_url: str) -> str:
    return (
        "#!ipxe\n"
        "\n"
        "# Obtenir une IP si pas encore configurée (EFI peut déjà l'avoir fait)\n"
        "isset ${ip} || dhcp || dhcp net0 || dhcp net1\n"
        "\n"
        ":retry\n"
        f"chain --autofree {menu_url} || goto load_error\n"
        "exit\n"
        "\n"
        ":load_error\n"
        f"echo iPXE : impossible de charger {menu_url}\n"
        "sleep 5\n"
        "isset ${ip} || dhcp || dhcp net0 || dhcp net1\n"
        "goto retry\n"
    )


def ensure_ipxe_sources(
    src_dir: Path,
    logs: list[str],
    run: Callable[[list[str], Path | None], str],
) -> None:
    build_dir = Path(settings.build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    if (src_dir / ".git").exists():
        logs.append("Sources iPXE déjà présentes — git pull")
        run(["git", "pull", "--ff-only"], src_dir)
    else:
        logs.append("Clonage du dépôt iPXE (peut prendre quelques minutes)…")
        run(
            [
                "git",
                "clone",
                "--depth=1",
                "https://github.com/ipxe/ipxe.git",
                str(src_dir),
            ],
            None,
        )


def compile_ipxe_firmware(
    menu_url: str,
    *,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """
    Clone ou met à jour iPXE, patche general.h (HTTPS + console), compile et copie en TFTP.
    """
    progress = on_progress or _noop_progress
    logs: list[str] = []
    completed_steps: list[str] = []
    tftp_dir = Path(settings.tftp_root)
    src_dir = settings.ipxe_src_dir
    build_dir = Path(settings.build_dir)

    def run(cmd: list[str], cwd: Path | None = None) -> str:
        logs.append(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=1800,
            )
            out = result.stdout
            logs.append(out[-4000:] if len(out) > 4000 else out)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Commande échouée (code {result.returncode}) : {' '.join(cmd)}\n{out[-2000:]}"
                )
            return out
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timeout dépassé pour : {' '.join(cmd)}") from exc

    progress("init", completed_steps, logs)
    build_dir.mkdir(parents=True, exist_ok=True)
    tftp_dir.mkdir(parents=True, exist_ok=True)

    progress("git", completed_steps, logs)
    ensure_ipxe_sources(src_dir, logs, run)
    completed_steps.append("git")

    embed_content = build_embed_ipxe(menu_url)
    embed_path = src_dir / "src" / "embed.ipxe"
    progress("embed", completed_steps, logs)
    embed_path.write_text(embed_content, encoding="utf-8")
    logs.append(f"embed.ipxe généré :\n{embed_content}")
    completed_steps.append("embed")

    make_dir = src_dir / "src"

    progress("patch_ipxe_config", completed_steps, logs)
    logs.append("Patch config iPXE (CONSOLE_CMD + CONSOLE_FRAMEBUFFER pour pcbios)…")
    patch_ipxe_graphical_console_headers(src_dir, logs)
    completed_steps.append("patch_ipxe_config")

    progress("patch_ipxe_https", completed_steps, logs)
    logs.append("Patch config iPXE (DOWNLOAD_PROTO_HTTPS)…")
    patch_ipxe_https_support(src_dir, logs)
    tls_args = ipxe_tls_make_args()
    if tls_args:
        logs.append(
            f"Compilation avec {' '.join(tls_args)} (CERT/TRUST en fin de ligne make)."
        )
    else:
        logs.append(
            "Attention : /srv/ipxe/ssl/ca.crt absent — HTTPS sans TRUST custom."
        )
    completed_steps.append("patch_ipxe_https")

    progress("compile_bios", completed_steps, logs)
    logs.append("Compilation undionly.kpxe (BIOS)…")
    run(["make", "bin/undionly.kpxe", "EMBED=embed.ipxe", *tls_args], make_dir)
    completed_steps.append("compile_bios")

    progress("compile_efi", completed_steps, logs)
    logs.append("Compilation snponly.efi (UEFI SNP)…")
    run(["make", "bin-x86_64-efi/snponly.efi", "EMBED=embed.ipxe", *tls_args], make_dir)
    logs.append("Compilation ipxe.efi (UEFI bare)…")
    run(["make", "bin-x86_64-efi/ipxe.efi", "EMBED=embed.ipxe", *tls_args], make_dir)
    completed_steps.append("compile_efi")

    progress("copy", completed_steps, logs)
    logs.append(f"Copie des binaires vers {tftp_dir}")
    kpxe_src = make_dir / "bin" / "undionly.kpxe"
    efi_src = make_dir / "bin-x86_64-efi" / "ipxe.efi"
    snponly_src = make_dir / "bin-x86_64-efi" / "snponly.efi"

    shutil.copy2(kpxe_src, tftp_dir / "undionly.kpxe")
    shutil.copy2(efi_src, tftp_dir / "ipxe.efi")
    shutil.copy2(snponly_src, tftp_dir / "snponly.efi")
    for fname in ("undionly.kpxe", "ipxe.efi", "snponly.efi"):
        (tftp_dir / fname).chmod(0o644)
    completed_steps.append("copy")

    logs.append("Compilation terminée avec succès.")
    return {
        "status": "success",
        "menu_url": menu_url,
        "embed": embed_content,
        "undionly": str(tftp_dir / "undionly.kpxe"),
        "efi": str(tftp_dir / "ipxe.efi"),
        "snponly": str(tftp_dir / "snponly.efi"),
        "logs": "\n".join(logs),
        "completed_steps": completed_steps,
    }
