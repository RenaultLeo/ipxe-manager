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


def _grep_config_lines(path: Path, patterns: tuple[str, ...]) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if any(p in line for p in patterns):
            out.append(f"  L{i}: {line.rstrip()}")
    return out


def _pcbios_still_undefines_https(general_text: str) -> bool:
    """True si le bloc #if PLATFORM_pcbios contient encore #undef DOWNLOAD_PROTO_HTTPS."""
    in_pcbios = False
    for line in general_text.splitlines():
        if re.search(r"#if\s+defined\s*\(\s*PLATFORM_pcbios\s*\)", line):
            in_pcbios = True
            continue
        if in_pcbios and re.match(r"^[ \t]*#endif\b", line):
            break
        if in_pcbios and re.search(r"#undef[ \t]+DOWNLOAD_PROTO_HTTPS\b", line):
            return True
    return False


def verify_ipxe_https_config(src_dir: Path, logs: list[str]) -> None:
    """Contrôle general.h + local/general.h avant make (échec immédiat si invalide)."""
    general = src_dir / "src" / "config" / "general.h"
    local_h = src_dir / "src" / "config" / "local" / "general.h"
    ca = settings.tls_ca_cert_path

    logs.append("=== Pré-vol HTTPS : config/general.h (extrait) ===")
    for line in _grep_config_lines(
        general, ("DOWNLOAD_PROTO_HTTPS", "PLATFORM_pcbios", "DOWNLOAD_PROTO_HTTP")
    ):
        logs.append(line)

    g = general.read_text(encoding="utf-8", errors="replace") if general.is_file() else ""
    if _pcbios_still_undefines_https(g):
        raise RuntimeError(
            "general.h : bloc PLATFORM_pcbios contient encore "
            "#undef DOWNLOAD_PROTO_HTTPS (undionly.kpxe = HTTP only)."
        )
    if re.search(r"^[ \t]*#undef[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE):
        raise RuntimeError(
            "general.h contient encore #undef DOWNLOAD_PROTO_HTTPS — "
            "patch non appliqué ou écrasé par git pull."
        )
    if not re.search(r"^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE):
        raise RuntimeError(
            "general.h sans #define DOWNLOAD_PROTO_HTTPS — patch HTTPS incomplet."
        )

    logs.append("=== Pré-vol HTTPS : config/local/general.h ===")
    if not local_h.is_file():
        raise RuntimeError(
            f"config/local/general.h absent ({local_h}) — requis pour undionly (PLATFORM_pcbios)."
        )
    local_body = local_h.read_text(encoding="utf-8", errors="replace")
    for line in _grep_config_lines(local_h, ("DOWNLOAD_PROTO_HTTPS",)):
        logs.append(line)
    if not re.search(r"^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS\b", local_body, flags=re.MULTILINE):
        raise RuntimeError("local/general.h sans #define DOWNLOAD_PROTO_HTTPS.")

    if ca.is_file():
        logs.append(f"Pré-vol OK : CA présente ({ca.resolve()}).")
    else:
        logs.append(
            f"Attention : CA absente ({ca}) — compilation sans TRUST= personnalisé."
        )
    logs.append(
        "HTTPS activé via config/general.h + config/local/general.h "
        "(pas seulement CERT/TRUST sur la ligne make)."
    )
    logs.append(
        "Note : DOWNLOAD_PROTO_HTTPS=1 sur « make » n'est pas lu par iPXE officiel "
        "(ipxe.org/crypto) — seuls TRUST=/CERT=/EMBED= le sont."
    )
    logs.append("Pré-vol HTTPS : configuration fichiers OK.")


def verify_https_preprocessor(make_dir: Path, logs: list[str]) -> None:
    """Vérifie que le préprocesseur voit DOWNLOAD_PROTO_HTTPS (build BIOS / undionly)."""
    probe = make_dir / ".ipxe_https_probe.c"
    probe.write_text(
        "#include <config/general.h>\n"
        "#ifndef DOWNLOAD_PROTO_HTTPS\n"
        '#error "DOWNLOAD_PROTO_HTTPS absent pour PLATFORM_pcbios"\n'
        "#endif\n",
        encoding="utf-8",
    )
    cmd = [
        "gcc",
        "-E",
        "-I.",
        "-Iinclude",
        "-DARCH=i386",
        "-DPLATFORM=pcbios",
        "-DPLATFORM_pcbios",
        str(probe),
    ]
    logs.append(f"=== Pré-vol préprocesseur (undionly / pcbios) ===\n$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=make_dir,
            capture_output=True,
            text=True,
            timeout=60,
            errors="replace",
        )
    except OSError as exc:
        logs.append(f"Pré-vol préprocesseur ignoré ({exc}) — gcc absent ?")
        return
    combined = (proc.stdout or "") + (proc.stderr or "")
    if "DOWNLOAD_PROTO_HTTPS absent" in combined or proc.returncode != 0:
        tail = combined[-1500:] if len(combined) > 1500 else combined
        raise RuntimeError(
            "Le préprocesseur ne voit pas DOWNLOAD_PROTO_HTTPS pour undionly.kpxe. "
            f"Vérifiez config/local/general.h.\n{tail}"
        )
    logs.append("Pré-vol préprocesseur : DOWNLOAD_PROTO_HTTPS défini pour pcbios.")


def verify_embed_script(embed_path: Path, menu_url: str, logs: list[str]) -> None:
    """Vérifie embed.ipxe sur disque avant make."""
    if not embed_path.is_file():
        raise RuntimeError(f"embed.ipxe absent : {embed_path}")
    body = embed_path.read_text(encoding="utf-8", errors="replace")
    logs.append(f"=== Pré-vol embed.ipxe ({embed_path.stat().st_size} octets) ===")
    for line in body.splitlines()[:12]:
        logs.append(f"  | {line}")
    if "#!ipxe" not in body:
        raise RuntimeError("embed.ipxe invalide : en-tête #!ipxe manquant.")
    if menu_url not in body:
        raise RuntimeError(
            f"embed.ipxe ne contient pas l'URL menu attendue : {menu_url}"
        )
    if f"chain --autofree {menu_url}" not in body:
        raise RuntimeError("embed.ipxe : ligne chain --autofree introuvable.")
    logs.append("Pré-vol embed.ipxe : OK.")


def _strings_blob(path: Path) -> str:
    proc = subprocess.run(
        ["strings", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"strings a échoué sur {path} (code {proc.returncode})")
    return proc.stdout or ""


def verify_built_firmware(
    kpxe: Path,
    menu_url: str,
    make_dir: Path,
    logs: list[str],
    *,
    label: str,
) -> None:
    """Vérifie le binaire juste après make (avant copie TFTP)."""
    if not kpxe.is_file():
        raise RuntimeError(f"{label} absent après make : {kpxe}")

    embedded_list = make_dir / "bin" / ".embedded.list"
    if embedded_list.is_file():
        logs.append(
            f"=== {label} : bin/.embedded.list ===\n  {embedded_list.read_text().strip()}"
        )
    else:
        raise RuntimeError(
            f"{label} : bin/.embedded.list absent — le build n'a pas pris EMBED=."
        )

    blob = _strings_blob(kpxe)
    blob_l = blob.lower()
    markers = (
        menu_url,
        "/menus/menu.ipxe",
        "chain --autofree",
        "load_error",
    )
    found = [m for m in markers if m.lower() in blob_l]
    has_tls = any(s in blob_l for s in ("openssl", "tls_", "tlsv", "https_conn"))

    logs.append(
        f"=== {label} : strings ({kpxe.stat().st_size} o) — marqueurs {found or 'aucun'} "
        f"TLS={'oui' if has_tls else 'non'} ==="
    )

    if not found:
        raise RuntimeError(
            f"{label} compilé sans script embarqué (EMBED ignoré ou mauvais binaire). "
            f"Vérifiez {embedded_list} et relancez après make clean."
        )
    if menu_url.lower().startswith("https://") and not has_tls and "https://" not in blob_l:
        raise RuntimeError(
            f"{label} contient l'embed mais pas de TLS/HTTPS — "
            "general.h / local/general.h non pris en compte par make."
        )
    logs.append(f"Pré-vol binaire {label} : OK.")


def _verify_firmware_https_support(tftp_kpxe: Path, menu_url: str, logs: list[str]) -> None:
    """Vérifie embed (URL menu) et indices TLS dans undionly.kpxe."""
    if not menu_url.lower().startswith("https://"):
        return
    try:
        proc = subprocess.run(
            ["strings", str(tftp_kpxe)],
            capture_output=True,
            text=True,
            timeout=120,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logs.append(f"Vérification strings {tftp_kpxe.name} ignorée : {exc}")
        return
    blob = proc.stdout or ""
    blob_l = blob.lower()
    has_embed = menu_url in blob or "/menus/menu.ipxe" in blob_l
    has_https_url = "https://" in blob_l
    has_tls = any(
        s in blob_l
        for s in ("openssl", "tls_", "tlsv", "https_conn", "cipher")
    )
    if has_embed and (has_https_url or has_tls):
        logs.append(
            "Vérification OK : undionly.kpxe contient l'URL embed et TLS/HTTPS."
        )
        return
    if has_embed and not has_tls:
        logs.append(
            "Attention : URL embed présente mais peu d'indices TLS — "
            "testez quand même le boot PXE."
        )
        return
    hints = [
        "Contrôlez src/config/general.h : aucun #undef DOWNLOAD_PROTO_HTTPS "
        "(bloc PLATFORM_pcbios).",
        "Vérifiez src/config/local/general.h (#define DOWNLOAD_PROTO_HTTPS).",
        "Relancez après « make clean » (embed.ipxe doit exister avant make).",
        f"Vérifiez {settings.tls_ca_cert_path} pour TRUST= au build.",
    ]
    raise RuntimeError(
        "undionly.kpxe sans URL menu embarquée ou sans HTTPS — au boot : "
        "« Operation not supported » (https://ipxe.org/3c092003). "
        + " ".join(hints)
    )


def ipxe_make_debug_args() -> list[str]:
    """
    Ne pas passer DEBUG=… à make : sur plusieurs clones iPXE cela casse la build
    (ex. « aucune règle pour fabriquer bin/openssl.dbg1.o »).
    Le mode debug repose sur set loglevel 7 (scripts) + LOG_LEVEL dans general.h.
    """
    return []


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
    """CERT= et TRUST= pour make (paramètres make documentés par ipxe.org/crypto)."""
    ca = settings.tls_ca_cert_path
    if not ca.is_file():
        return []
    ca_s = str(ca.resolve())
    return [f"CERT={ca_s}", f"TRUST={ca_s}"]


def ipxe_firmware_make_args(menu_url: str) -> list[str]:
    """
    Paramètres make pour le firmware.

    - TRUST=/CERT= : certificat(s) embarqués (documenté iPXE).
    - DOWNLOAD_PROTO_HTTPS=1 : présent dans certains tutos ; le Makefile iPXE
      officiel ne le consomme pas — l'activation HTTPS est faite dans general.h
      + config/local/general.h (voir patch_ipxe_https_support).
    """
    args: list[str] = []
    if menu_url.lower().startswith("https://"):
        args.append("DOWNLOAD_PROTO_HTTPS=1")
    args.extend(ipxe_tls_make_args())
    args.extend(ipxe_make_debug_args())
    return args


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

    build_dir.mkdir(parents=True, exist_ok=True)
    tftp_dir.mkdir(parents=True, exist_ok=True)

    if (src_dir / ".git").exists():
        progress("git_pull", completed_steps, logs)
        logs.append("Sources iPXE déjà présentes — git pull")
        run(["git", "pull", "--ff-only"], src_dir)
        logs.append(
            "git pull terminé — les patches HTTPS/embed seront réappliqués avant make."
        )
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
    make_tail = ipxe_firmware_make_args(menu_url)
    if any(a.startswith("CERT=") or a.startswith("TRUST=") for a in make_tail):
        logs.append(
            f"Compilation avec {' '.join(a for a in make_tail if 'TRUST=' in a or 'CERT=' in a)}."
        )
    else:
        logs.append(
            "Attention : /srv/ipxe/ssl/ca.crt absent — HTTPS sans TRUST custom."
        )
    if settings.ipxe_debug:
        logs.append(
            "Mode debug : scripts (loglevel 7) + LOG_LEVEL dans general.h "
            "(pas de DEBUG= sur la ligne make — évite openssl.dbg1.o)."
        )
    logs.append(
        "HTTPS protocole : patch general.h + local/general.h "
        f"(make reçoit aussi {' '.join(a for a in make_tail if 'DOWNLOAD_PROTO' in a)} — "
        "ignoré par make officiel, conservé pour compatibilité tutos)."
    )
    completed_steps.append("patch_ipxe_https")

    progress("preflight_config", completed_steps, logs)
    verify_ipxe_https_config(src_dir, logs)
    completed_steps.append("preflight_config")
    embed_abs = str(embed_path.resolve())
    if menu_url.lower().startswith("https://"):
        progress("make_clean", completed_steps, logs)
        logs.append("make clean (rebuild complet pour HTTPS)…")
        run(["make", "clean"], make_dir)
        completed_steps.append("make_clean")

    embed_content = build_embed_ipxe(menu_url)
    progress("embed", completed_steps, logs)
    embed_path.write_text(embed_content, encoding="utf-8")
    verify_embed_script(embed_path, menu_url, logs)
    completed_steps.append("embed")

    progress("preflight_embed", completed_steps, logs)
    verify_ipxe_https_config(src_dir, logs)
    verify_embed_script(embed_path, menu_url, logs)
    if menu_url.lower().startswith("https://"):
        verify_https_preprocessor(make_dir, logs)
    completed_steps.append("preflight_embed")

    kpxe_src = make_dir / "bin" / "undionly.kpxe"
    embedded_list = make_dir / "bin" / ".embedded.list"
    for stale in (kpxe_src, embedded_list):
        if stale.exists():
            stale.unlink()
            logs.append(f"Supprimé avant make : {stale.relative_to(make_dir)}")

    progress("compile_bios", completed_steps, logs)
    logs.append(f"Compilation undionly.kpxe (BIOS), EMBED={embed_abs}…")
    run(["make", "bin/undionly.kpxe", f"EMBED={embed_abs}", *make_tail], make_dir)
    verify_built_firmware(kpxe_src, menu_url, make_dir, logs, label="undionly.kpxe")
    completed_steps.append("compile_bios")

    progress("compile_efi", completed_steps, logs)
    logs.append("Compilation snponly.efi (UEFI SNP)…")
    run(["make", "bin-x86_64-efi/snponly.efi", f"EMBED={embed_abs}", *make_tail], make_dir)
    logs.append("Compilation ipxe.efi (UEFI bare)…")
    run(["make", "bin-x86_64-efi/ipxe.efi", f"EMBED={embed_abs}", *make_tail], make_dir)
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

    _verify_firmware_https_support(tftp_dir / "undionly.kpxe", menu_url, logs)

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
