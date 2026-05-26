"""
Compilation firmware iPXE — clone/pull, patch general.h (HTTPS), make, copie TFTP.
Utilisé par Celery (compile_ipxe_task) et deploy/compile_ipxe_firmware.py (setup).
"""
from __future__ import annotations

import hashlib
import logging
import os
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


def patch_ipxe_debug_support(src_dir: Path, logs: list[str], *, enable: bool) -> None:
    """LOG_LEVEL élevé + symbole DEBUG pour traces http/tls sur la console iPXE."""
    if not enable:
        logs.append("config/general.h : mode debug firmware désactivé (IPXE_DEBUG=false).")
        return

    general = src_dir / "src" / "config" / "general.h"
    if not general.is_file():
        raise RuntimeError(f"Sources iPXE incomplètes : {general}")

    g = general.read_text(encoding="utf-8", errors="replace")
    changed = False

    if re.search(r"^[ \t]*#define[ \t]+LOG_LEVEL[ \t]+LOG_", g, flags=re.MULTILINE):
        g2, n = re.subn(
            r"^[ \t]*#define[ \t]+LOG_LEVEL[ \t]+LOG_[A-Z_0-9]+",
            "#define LOG_LEVEL LOG_ALL",
            g,
            count=1,
            flags=re.MULTILINE,
        )
        if n:
            g = g2
            changed = True
            logs.append("config/general.h : LOG_LEVEL → LOG_ALL.")

    if changed:
        general.write_text(g, encoding="utf-8")
    else:
        logs.append("config/general.h : LOG_LEVEL déjà adapté ou format inattendu.")


def _tls_ca_stamp_path(make_dir: Path) -> Path:
    return make_dir / ".ipxe_manager_tls_ca_stamp"


def _tls_ca_changed_since_last_build(make_dir: Path, logs: list[str]) -> bool:
    """True si la CA embarquée (TRUST=) a changé — seul cas où make clean est utile."""
    stamp = _tls_ca_stamp_path(make_dir)
    ca = settings.tls_ca_cert_path
    if not ca.is_file():
        if stamp.is_file():
            stamp.unlink(missing_ok=True)
        return False
    digest = hashlib.sha256(ca.read_bytes()).hexdigest()
    prev = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
    if prev == digest:
        return False
    stamp.write_text(digest, encoding="utf-8")
    if prev:
        logs.append("CA TLS modifiée — make clean (recompile liée OpenSSL/TLS).")
        return True
    logs.append("Première compilation avec CA TLS — pas de make clean (build incrémental).")
    return False


def _purge_embed_build_artifacts(make_dir: Path, bin_subdir: str, logs: list[str]) -> None:
    """Force la regénération de embedded.o (.incbin / ccache ne voient pas toujours embed.ipxe)."""
    bin_dir = make_dir / bin_subdir
    if not bin_dir.is_dir():
        return
    removed: list[str] = []
    for path in sorted(bin_dir.iterdir()):
        name = path.name
        if name == ".embedded.list" or name.startswith("embedded."):
            path.unlink(missing_ok=True)
            removed.append(name)
    if removed:
        logs.append(
            f"Purge {bin_subdir}/ : {', '.join(removed)} (rebuild embedded obligatoire)."
        )


def ensure_ipxe_local_https_override(src_dir: Path, logs: list[str]) -> None:
    """
    general.h fait #undef DOWNLOAD_PROTO_HTTPS pour PLATFORM_pcbios (undionly).
    local/general.h est inclus en dernier et réactive HTTPS pour le BIOS.
    """
    local_dir = src_dir / "src" / "config" / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_h = local_dir / "general.h"
    marker = "iPXE Manager"
    body = (
        f"/* {marker} — HTTPS pour undionly.kpxe (après #undef PLATFORM_pcbios) */\n"
        "#define DOWNLOAD_PROTO_HTTPS\n"
    )
    prev = local_h.read_text(encoding="utf-8", errors="replace") if local_h.is_file() else ""
    if prev != body:
        local_h.write_text(body, encoding="utf-8")
        logs.append(f"config/local/general.h : #define DOWNLOAD_PROTO_HTTPS ({marker}).")
    else:
        logs.append("config/local/general.h : déjà configuré pour HTTPS.")


def patch_ipxe_https_support(src_dir: Path, logs: list[str]) -> None:
    """Active DOWNLOAD_PROTO_HTTPS (supprime #undef résiduels, un seul #define)."""
    general = src_dir / "src" / "config" / "general.h"
    if not general.is_file():
        raise RuntimeError(f"Sources iPXE incomplètes : {general}")

    g = general.read_text(encoding="utf-8", errors="replace")
    # #undef désactive le protocole même si un #define existe plus loin
    g, n_undef = re.subn(
        r"^[ \t]*#undef[ \t]+DOWNLOAD_PROTO_HTTPS[^\n]*\n",
        "",
        g,
        flags=re.MULTILINE,
    )
    g, n_comment = re.subn(
        r"^[ \t]*//[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS[^\n]*\n",
        "#define DOWNLOAD_PROTO_HTTPS\t\t/* Secure Hypertext Transfer Protocol */\n",
        g,
        count=1,
        flags=re.MULTILINE,
    )
    has_define = bool(
        re.search(r"^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE)
    )
    if not has_define:
        g2, n_ins = re.subn(
            r"(^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTP\b[^\n]*\n)",
            r"\1#define DOWNLOAD_PROTO_HTTPS\t\t/* Secure Hypertext Transfer Protocol */\n",
            g,
            count=1,
            flags=re.MULTILINE,
        )
        if n_ins:
            g = g2
        else:
            raise RuntimeError(
                "config/general.h : impossible d'insérer #define DOWNLOAD_PROTO_HTTPS "
                "(structure iPXE inattendue)."
            )

    if n_undef:
        logs.append(f"config/general.h : {n_undef} ligne(s) #undef DOWNLOAD_PROTO_HTTPS supprimée(s).")
    if n_comment:
        logs.append("config/general.h : #define DOWNLOAD_PROTO_HTTPS décommenté.")
    if has_define and not n_undef and not n_comment:
        logs.append("config/general.h : #define DOWNLOAD_PROTO_HTTPS déjà présent.")
    elif not n_undef and not n_comment:
        logs.append("config/general.h : #define DOWNLOAD_PROTO_HTTPS ajouté après HTTP.")

    if re.search(r"^[ \t]*#undef[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE):
        raise RuntimeError(
            "config/general.h : #undef DOWNLOAD_PROTO_HTTPS encore présent après patch."
        )

    general.write_text(g, encoding="utf-8")
    ensure_ipxe_local_https_override(src_dir, logs)


def ipxe_tls_make_args() -> list[str]:
    """CERT= et TRUST= pour make (derniers arguments de la ligne make)."""
    ca = settings.tls_ca_cert_path
    if not ca.is_file():
        return []
    ca_s = str(ca.resolve())
    return [f"CERT={ca_s}", f"TRUST={ca_s}"]


def build_embed_ipxe(menu_url: str, *, debug: bool | None = None) -> str:
    dbg = settings.ipxe_debug if debug is None else debug
    lines = [
        "#!ipxe",
        "",
    ]
    if dbg:
        lines.extend(
            [
                "# Mode debug iPXE Manager (IPXE_DEBUG)",
                "set loglevel 7",
                "",
            ]
        )
    lines.extend(
        [
            "# Obtenir une IP si pas encore configurée (EFI peut déjà l'avoir fait)",
            "isset ${ip} || dhcp || dhcp net0 || dhcp net1",
            "",
            ":retry",
            f"chain --autofree {menu_url} || goto load_error",
            "exit",
            "",
            ":load_error",
            "echo ========================================",
            "echo iPXE : echec chain menu",
            f"echo URL : {menu_url}",
        ]
    )
    if dbg:
        lines.extend(
            [
                "echo errno : ${errno}",
                "echo errmsg : ${errmsg}",
                "ifstat",
                "route",
                "echo ========================================",
                "sleep 15",
            ]
        )
    else:
        lines.append("sleep 5")
    lines.extend(
        [
            "isset ${ip} || dhcp || dhcp net0 || dhcp net1",
            "goto retry",
            "",
        ]
    )
    return "\n".join(lines)


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

    make_env = os.environ.copy()

    def run(cmd: list[str], cwd: Path | None = None) -> str:
        logs.append(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                env=make_env,
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

    build_dir.mkdir(parents=True, exist_ok=True)
    tftp_dir.mkdir(parents=True, exist_ok=True)

    if (src_dir / ".git").exists():
        progress("git_pull", completed_steps, logs)
        logs.append("Sources iPXE déjà présentes — git pull")
        run(["git", "pull", "--ff-only"], src_dir)
        completed_steps.append("git_clone")
        completed_steps.append("git_pull")
    else:
        progress("git_clone", completed_steps, logs)
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
        completed_steps.append("git_clone")

    embed_path = src_dir / "src" / "embed.ipxe"
    make_dir = src_dir / "src"

    progress("patch_ipxe_config", completed_steps, logs)
    logs.append("Patch config iPXE (CONSOLE_CMD + CONSOLE_FRAMEBUFFER pour pcbios)…")
    patch_ipxe_graphical_console_headers(src_dir, logs)
    completed_steps.append("patch_ipxe_config")

    progress("patch_ipxe_https", completed_steps, logs)
    logs.append("Patch config iPXE (DOWNLOAD_PROTO_HTTPS)…")
    patch_ipxe_https_support(src_dir, logs)
    progress("patch_ipxe_debug", completed_steps, logs)
    logs.append("Patch config iPXE (DEBUG / LOG_LEVEL)…")
    patch_ipxe_debug_support(src_dir, logs, enable=settings.ipxe_debug)
    completed_steps.append("patch_ipxe_debug")

    # Pas de DEBUG= sur la ligne make (casse openssl.dbg1.o sur certains clones).
    make_tail = ipxe_tls_make_args()
    if tls_args := [a for a in make_tail if a.startswith("TRUST=")]:
        logs.append(f"Compilation avec {' '.join(tls_args)}.")
    elif menu_url.lower().startswith("https://"):
        logs.append(
            f"Attention : {settings.tls_ca_cert_path} absent — HTTPS sans TRUST custom."
        )
    if settings.ipxe_debug:
        logs.append(
            "Mode debug : scripts (loglevel 7) + LOG_LEVEL dans general.h "
            "(pas de DEBUG= sur la ligne make — évite openssl.dbg1.o)."
        )
    completed_steps.append("patch_ipxe_https")

    if _tls_ca_changed_since_last_build(make_dir, logs):
        progress("make_clean", completed_steps, logs)
        run(["make", "clean"], make_dir)
        completed_steps.append("make_clean")

    embed_content = build_embed_ipxe(menu_url)
    progress("embed", completed_steps, logs)
    embed_path.write_text(embed_content, encoding="utf-8")
    if menu_url not in embed_path.read_text(encoding="utf-8"):
        raise RuntimeError(f"embed.ipxe invalide : URL menu absente ({menu_url})")
    logs.append(f"embed.ipxe généré :\n{embed_content}")
    completed_steps.append("embed")

    kpxe_src = make_dir / "bin" / "undionly.kpxe"
    _purge_embed_build_artifacts(make_dir, "bin", logs)

    progress("compile_bios", completed_steps, logs)
    logs.append("Compilation undionly.kpxe (BIOS), EMBED=embed.ipxe…")
    run(["make", "bin/undionly.kpxe", "EMBED=embed.ipxe", *make_tail], make_dir)
    if not kpxe_src.is_file():
        raise RuntimeError("undionly.kpxe absent après make")
    completed_steps.append("compile_bios")

    progress("compile_efi", completed_steps, logs)
    _purge_embed_build_artifacts(make_dir, "bin-x86_64-efi", logs)
    logs.append("Compilation snponly.efi (UEFI SNP)…")
    run(["make", "bin-x86_64-efi/snponly.efi", "EMBED=embed.ipxe", *make_tail], make_dir)
    logs.append("Compilation ipxe.efi (UEFI bare)…")
    run(["make", "bin-x86_64-efi/ipxe.efi", "EMBED=embed.ipxe", *make_tail], make_dir)
    completed_steps.append("compile_efi")

    progress("copy", completed_steps, logs)
    logs.append(f"Copie des binaires vers {tftp_dir}")
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
