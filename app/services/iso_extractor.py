"""
Extracteur ISO par distribution.
Utilise 7z pour extraire l'ISO, puis cherche les fichiers de boot
avec des règles spécifiques à chaque distro.
"""
from __future__ import annotations

import json
import logging
import subprocess
import shutil
import tempfile
import re
from pathlib import PurePosixPath, Path
from urllib.parse import unquote, urlparse

from app.config import settings

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


# ── Règles par distribution ────────────────────────────────────────────────────
# Chaque entrée : liste de noms exacts OU préfixes (terminant par "*")

DISTRO_RULES: dict[str, dict] = {
    # Windows / WinPE
    "windows": {
        "type":   "windows",
        "kernel": [],
        "initrd": [],
        "extra":  {},
    },
    "winpe": {
        "type":   "windows",
        "kernel": [],
        "initrd": [],
        "extra":  {},
    },
    # Debian — vmlinuz + initrd.gz
    "debian": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.gz", "initrd"],
        "extra":   {},
    },
    # Ubuntu Server — extraction complète (cloud-init a besoin des fichiers de l'ISO)
    # vmlinuz et initrd sont dans casper/ ; user-data/meta-data créés par l'utilisateur
    "ubuntu": {
        "type":    "ubuntu",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd", "initrd.gz", "initrd.lz", "initrd.lz4"],
        "extra":   {},
    },
    # CentOS / Rocky / AlmaLinux / Fedora — vmlinuz + initrd.img
    "centos": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    "rocky": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    "alma": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    "fedora": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    # Proxmox VE — linux26 (v7) ou vmlinuz (v8) + initrd.img
    "proxmox": {
        "type":    "linux",
        "kernel":  ["linux26", "vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    # Alpine Linux — vmlinuz-lts + initramfs-lts + modloop-lts
    "alpine": {
        "type":    "linux",
        "kernel":  ["vmlinuz-lts", "vmlinuz"],
        "initrd":  ["initramfs-lts", "initramfs"],
        "extra":   {"modloop": ["modloop-lts", "modloop"]},
    },
    # VMware ESXi — mboot.c32 + boot.cfg (+ kernel/module listés dans boot.cfg),
    # boot réseau classique décrit dans la doc d'installation (« About the boot.cfg file »).
    "esxi": {
        "type":    "esxi",
        "kernel":  [],
        "initrd":  [],
        "extra":   {},
    },
}

# Règle générique fallback pour les distros non listées
_GENERIC_RULE: dict = {
    "type":   "linux",
    "kernel": ["vmlinuz", "vmlinux", "linux26", "kernel"],
    "initrd": ["initrd.gz", "initrd.img", "initrd", "initramfs.img", "initramfs"],
    "extra":  {},
}

# Extensions initrd acceptées
INITRD_EXTENSIONS = {"", ".gz", ".lz", ".lz4", ".xz", ".zst", ".img", ".cpio"}

# Noms Windows attendus
WIN_EXACT = {
    "bcd":       ("BCD",       "bcd_path"),
    "boot.sdi":  ("boot.sdi",  "boot_sdi_path"),
    "boot.wim":  ("boot.wim",  "boot_wim_path"),
}


# ── Point d'entrée public ──────────────────────────────────────────────────────

def extract_iso(iso_path: str, os_slug: str, version_id: int, version_label: str = "") -> dict:
    from app.services.slugify import slugify
    version_slug = slugify(version_label) if version_label else str(version_id)

    iso = Path(iso_path)
    if not iso.exists():
        raise ExtractionError(f"ISO introuvable : {iso_path}")

    seven_z = shutil.which("7z") or shutil.which("7za")
    if not seven_z:
        raise ExtractionError("7z non installé — apt-get install -y p7zip-full")

    dest = settings.boot_dir / os_slug / version_slug
    dest.mkdir(parents=True, exist_ok=True)

    logger.info("Extraction %s → %s", iso.name, dest)

    rule = DISTRO_RULES.get(os_slug, _GENERIC_RULE)

    if rule["type"] == "skip":
        raise ExtractionError(
            f"L'extraction automatique n'est pas supportée pour {os_slug}. "
            "Uploader les fichiers manuellement."
        )

    if rule["type"] == "esxi":
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [seven_z, "x", str(iso), f"-o{tmp}", "-y"],
                capture_output=True, text=True,
                timeout=settings.extract_timeout,
            )
            if proc.returncode not in (0, 1):
                raise ExtractionError(
                    f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
                )
            paths = _extract_esxi(Path(tmp), dest, os_slug, version_slug)
        _fix_permissions(dest)
        logger.info("Extraction terminée : %s", paths)
        return paths

    if rule["type"] in ("windows", "ubuntu"):
        # Extraction COMPLÈTE de l'ISO directement dans dest
        # Windows : tous les fichiers nécessaires pour setup.exe via Samba/HTTP
        # Ubuntu  : contenu ISO nécessaire pour cloud-init autoinstall via HTTP
        logger.info("Extraction complète %s → %s", os_slug, dest)
        proc = subprocess.run(
            [seven_z, "x", str(iso), f"-o{str(dest)}", "-y"],
            capture_output=True, text=True,
            timeout=settings.extract_timeout,
        )
        if proc.returncode not in (0, 1):
            raise ExtractionError(
                f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
            )
        _fix_permissions(dest)
        if rule["type"] == "windows":
            paths = _find_windows_in_dest(dest, os_slug, version_slug)
        else:
            paths = _find_ubuntu_in_dest(dest, os_slug, version_slug, rule)
    else:
        # Linux : extraction dans un dossier temp, copie des fichiers de boot seulement
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [seven_z, "x", str(iso), f"-o{tmp}", "-y"],
                capture_output=True, text=True,
                timeout=settings.extract_timeout,
            )
            if proc.returncode not in (0, 1):
                raise ExtractionError(
                    f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
                )
            paths = _find_linux(Path(tmp), dest, os_slug, version_slug, rule)

    logger.info("Extraction terminée : %s", paths)
    return paths


# ── VMware ESXi (mboot.c32 / boot.cfg) ───────────────────────────────────────────

_MODULES_SPLIT_RE = re.compile(r"\s*---\s*")


def _esxi_merge_cfg_continuations(raw: str) -> str:
    """Fusionne les lignes terminées par \\ (continuation typique VMware boot.cfg)."""
    merged: list[str] = []
    buf = ""
    for line in raw.replace("\r\n", "\n").split("\n"):
        s = line.rstrip("\r")
        if s.endswith("\\"):
            buf += s[:-1].rstrip()
        else:
            merged.append(buf + s)
            buf = ""
    if buf:
        merged.append(buf)
    return "\n".join(merged)


def _parse_esxi_boot_cfg_text(text: str) -> tuple[dict[str, str], list[str]]:
    """
    Lecture boot.cfg VMware : retourne (clés simples hors kernel/modules/module, liste ordonnée modules).
    """
    body = _esxi_merge_cfg_continuations(text)
    kv: dict[str, str] = {}
    mods: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip().lower()
        val = v.strip()
        if key == "module":
            if val:
                mods.append(val)
        elif key == "modules":
            for part in _MODULES_SPLIT_RE.split(val.strip()):
                if part.strip():
                    mods.append(part.strip())
        else:
            kv[key] = val
    return kv, mods


def _esxi_normalize_path(ref: str) -> str:
    """Enlève file:// ou http(s) ; retient le chemin / nom de fichier utile sous l'ISO."""
    ref = ref.strip().strip("\"'")
    if not ref:
        return ""
    low = ref.lower()
    if low.startswith("http://") or low.startswith("https://"):
        p = urlparse(ref)
        ref = unquote(p.path or "")
    elif low.startswith("file://"):
        p = urlparse(ref)
        ref = unquote(p.path or "")
    return ref.replace("\\", "/")


def _esxi_resolve_file(
    iso_root: Path,
    boot_cfg_dir: Path,
    prefix: str,
    ref: str,
    *,
    iso_index_by_lower: dict[str, list[Path]] | None = None,
) -> Path | None:
    """Résout un chemin mentionné dans boot.cfg vers un fichier sur l'ISO extraite."""
    ref = _esxi_normalize_path(ref)
    if not ref:
        return None
    pref = prefix.strip().replace("\\", "/").strip("/")
    rel = ref.lstrip("/")
    candidates: list[Path] = []
    if pref and not rel.lower().startswith(pref.lower() + "/") and rel != pref:
        candidates.append(iso_root / pref / rel)
        candidates.append(iso_root / pref / PurePosixPath(rel).name)
    candidates.append(iso_root / rel)
    candidates.append(boot_cfg_dir / PurePosixPath(rel).name)
    candidates.append(iso_root / PurePosixPath(rel).name)
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_file():
            return c
    # Fallback : même basename, casse différente (Linux + ISO VMware en majuscules)
    basename = PurePosixPath(rel).name
    if basename and iso_index_by_lower:
        pool = iso_index_by_lower.get(basename.lower(), [])
        if pool:
            return _esxi_pick_preferred_path(pool, iso_root)
    return None


def _esxi_index_files_casefold(iso_root: Path) -> dict[str, list[Path]]:
    """
    Indexe tous les fichiers de l’ISO par nom en minuscul (pour ISO 9660 / UDF en majuscules).
    Une clé peut pointer vers plusieurs chemins si doublons (rare).
    """
    idx: dict[str, list[Path]] = {}
    try:
        for p in iso_root.rglob("*"):
            if p.is_file():
                idx.setdefault(p.name.lower(), []).append(p)
    except OSError as exc:
        logger.warning("ESXi : parcours ISO pour index insensible à la casse — %s", exc)
    return idx


def _esxi_pick_preferred_path(paths: list[Path], iso_root: Path) -> Path:
    """Choisit un candidat lorsque plusieurs chemins ont le même nom (préfère le plus peu profond sous l’ISO)."""
    def sort_key(q: Path) -> tuple[int, int, str]:
        try:
            rel = q.relative_to(iso_root)
            depth = len(rel.parts)
        except ValueError:
            depth = 999
        return (depth, len(str(q)), str(q))

    return min(paths, key=sort_key)


def _pick_esxi_boot_cfg(iso_root: Path, idx: dict[str, list[Path]]) -> Path | None:
    """Choisit le boot.cfg ESXi pertinent (EFI ou racine OEM). Insensible à la casse (BOOT.CFG, etc.)."""
    candidates = sorted(
        idx.get("boot.cfg", []),
        key=lambda p: (len(p.relative_to(iso_root).parts), str(p)),
    )
    best: tuple[int, Path] | None = None
    for p in candidates:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = txt.lower()
        score = 0
        if "kernel=" in low:
            score += 2
        if "modules=" in low or re.search(r"(?m)^module\s*=", txt):
            score += 2
        if "kernelopt=" in low:
            score += 1
        if score and (best is None or score > best[0]):
            best = (score, p)
    return best[1] if best else None


def _rewrite_esxi_boot_cfg_flat(
    original_text: str,
    kernel_bn: str,
    module_basenames: list[str],
) -> str:
    """
    boot.cfg aplati pour HTTP : même répertoire pour mboot.c32, kernel, modules.
    Supprime prefix= et les lignes module= dispersées ; remplace kernel / modules par des basenames.
    """
    merged = _esxi_merge_cfg_continuations(original_text)
    out: list[str] = [
        "# iPXE Manager — boot.cfg pour boot HTTP (chemins relatifs au dossier mboot.c32/boot.cfg).",
    ]
    for raw in merged.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, _v = line.partition("=")
        key = k.strip().lower()
        if key in ("prefix", "module", "kernel", "modules"):
            continue
        out.append(line)
    out.append(f"kernel={kernel_bn}")
    if module_basenames:
        out.append("modules={}".format(" --- ".join(module_basenames)))
    return "\n".join(out) + "\n"


def _ensure_no_name_collision(paths: list[Path]) -> None:
    """Empêche deux sources différentes de partager le même nom de fichier aplati (insensible à la casse)."""
    by_low: dict[str, Path] = {}
    for p in paths:
        k = p.name.lower()
        if k in by_low and by_low[k].resolve() != p.resolve():
            raise ExtractionError(
                f"ESXi : collision de nom de fichier (« {by_low[k].name} » / « {p.name} ») — ISO incompatible."
            )
        by_low.setdefault(k, p)


def _extract_esxi(src: Path, dest: Path, os_slug: str, version_slug: str) -> dict:
    """Copie mboot.c32, boot.cfg réécrite, le kernel ESXi (.b00) et les modules nécessaires au PXE HTTP."""
    base = f"boot/{os_slug}/{version_slug}"
    iso_lower = _esxi_index_files_casefold(src)

    boot_cfg = _pick_esxi_boot_cfg(src, iso_lower)
    if not boot_cfg:
        raise ExtractionError("ESXi : aucun boot.cfg trouvé dans l'ISO.")

    raw_cfg = boot_cfg.read_text(encoding="utf-8", errors="replace")
    parsed, mod_refs = _parse_esxi_boot_cfg_text(raw_cfg)
    cfg_dir = boot_cfg.parent

    mboot_pool = iso_lower.get("mboot.c32", [])
    if not mboot_pool:
        raise ExtractionError("ESXi : mboot.c32 introuvable dans l'ISO (cherché sans tenir compte de la casse).")
    mboot_source = _esxi_pick_preferred_path(mboot_pool, src)
    if not mboot_source.is_file():
        raise ExtractionError("ESXi : mboot.c32 introuvable dans l'ISO.")

    prefix = parsed.get("prefix", "") or ""
    kernel_ref = parsed.get("kernel", "") or ""
    if not kernel_ref:
        raise ExtractionError("ESXi : pas de ligne kernel= dans boot.cfg.")

    k_path = _esxi_resolve_file(src, cfg_dir, prefix, kernel_ref, iso_index_by_lower=iso_lower)
    if not k_path:
        raise ExtractionError(
            f"ESXi : impossible de localiser le fichier kernel « {kernel_ref} » sur l'ISO."
        )

    if not mod_refs:
        raise ExtractionError(
            "ESXi : aucun module listé (modules= / module=) dans boot.cfg — ISO inattendu."
        )

    mod_refs_dedup: list[str] = []
    seen_r: set[str] = set()
    for ref in mod_refs:
        if ref not in seen_r:
            seen_r.add(ref)
            mod_refs_dedup.append(ref)
    mod_refs = mod_refs_dedup

    mod_paths: list[Path] = []
    for ref in mod_refs:
        p = _esxi_resolve_file(src, cfg_dir, prefix, ref, iso_index_by_lower=iso_lower)
        if not p or not p.is_file():
            raise ExtractionError(
                f"ESXi : module introuvable sur l'ISO : « {ref} »"
            )
        mod_paths.append(p)

    def copy_flat(path: Path) -> str:
        target = dest / path.name
        shutil.copy2(path, target)
        return path.name

    _ensure_no_name_collision([mboot_source, k_path, *mod_paths])

    mboot_name = copy_flat(mboot_source)
    kernel_bn = copy_flat(k_path)
    mod_bn = [copy_flat(p) for p in mod_paths]

    rewritten = _rewrite_esxi_boot_cfg_flat(
        raw_cfg,
        kernel_bn=kernel_bn,
        module_basenames=mod_bn,
    )
    (dest / "boot.cfg").write_text(rewritten, encoding="utf-8")

    modules_json = json.dumps(mod_bn, separators=(",", ":"))

    return {
        "kernel_path":          f"{base}/{mboot_name}",
        "esxi_boot_cfg_path": f"{base}/boot.cfg",
        "esxi_modules":       modules_json,
    }


# ── Linux ──────────────────────────────────────────────────────────────────────

def _find_linux(src: Path, dest: Path, os_slug: str, version_slug: str, rule: dict) -> dict:
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"
    kernel_names: list[str] = rule.get("kernel") or []
    initrd_names: list[str] = rule.get("initrd") or []
    extra: dict             = rule.get("extra") or {}

    # Si pas de règle précise → fallback générique
    if not kernel_names:
        kernel_names = _GENERIC_RULE["kernel"]
    if not initrd_names:
        initrd_names = _GENERIC_RULE["initrd"]

    # ── Kernel ──
    kernel = _find_by_priority(src, kernel_names, mode="kernel")
    if kernel:
        out_name = kernel.name  # conserver le nom d'origine (vmlinuz-lts, linux26…)
        shutil.copy2(kernel, dest / out_name)
        result["kernel_path"] = f"{base}/{out_name}"
        logger.info("Kernel : %s", out_name)
    else:
        logger.warning("Kernel non trouvé pour %s", os_slug)

    # ── Initrd ──
    initrd = _find_by_priority(src, initrd_names, mode="initrd")
    if initrd:
        out_name = initrd.name  # conserver le nom d'origine (initramfs-lts, initrd.img…)
        shutil.copy2(initrd, dest / out_name)
        result["initrd_path"] = f"{base}/{out_name}"
        logger.info("Initrd : %s", out_name)
    else:
        logger.warning("Initrd non trouvé pour %s", os_slug)

    # ── Fichiers extra (ex: modloop pour Alpine) ──
    for field_key, names in extra.items():
        f = _find_by_priority(src, names, mode="extra")
        if f:
            out_name = f.name   # conserver le nom d'origine (modloop-lts)
            shutil.copy2(f, dest / out_name)
            result[f"{field_key}_path"] = f"{base}/{out_name}"
            logger.info("Extra [%s] : %s", field_key, out_name)
        else:
            logger.warning("Extra [%s] non trouvé pour %s", field_key, os_slug)

    if not result:
        raise ExtractionError(
            f"Aucun fichier de boot trouvé dans l'ISO pour {os_slug}. "
            "Uploader les fichiers manuellement via Fichiers Boot."
        )
    return result


# ── Windows ────────────────────────────────────────────────────────────────────

def _find_windows_in_dest(dest: Path, os_slug: str, version_slug: str) -> dict:
    """
    Après extraction complète de l'ISO Windows dans `dest`,
    localise BCD, boot.sdi et boot.wim et retourne leurs chemins relatifs.
    Les fichiers restent à leur emplacement d'origine dans l'arborescence.
    """
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"

    searches = [
        ("bcd",      lambda f: f.name.upper() == "BCD" and not f.suffix, "bcd_path"),
        ("boot.sdi", lambda f: f.name.lower() == "boot.sdi",             "boot_sdi_path"),
        ("boot.wim", lambda f: f.name.lower() == "boot.wim",             "boot_wim_path"),
    ]

    for label, match_fn, field in searches:
        candidates = [f for f in dest.rglob("*") if f.is_file() and match_fn(f)]
        if candidates:
            chosen = max(candidates, key=lambda f: f.stat().st_size)
            # Chemin relatif à partir de dest
            rel = chosen.relative_to(dest)
            result[field] = f"{base}/{rel.as_posix()}"
            logger.info("%s détecté : %s", label, rel)
        else:
            logger.warning("%s non trouvé après extraction", label)

    if not result:
        raise ExtractionError(
            "Aucun fichier Windows (BCD / boot.sdi / boot.wim) trouvé dans l'ISO."
        )
    logger.info("Extraction Windows complète — %d fichiers de boot détectés", len(result))
    return result


# ── Ubuntu ─────────────────────────────────────────────────────────────────────

def _find_ubuntu_in_dest(dest: Path, os_slug: str, version_slug: str, rule: dict) -> dict:
    """
    Après extraction complète de l'ISO Ubuntu dans `dest`,
    localise vmlinuz et initrd dans l'arborescence (typiquement casper/).
    Les fichiers restent en place — le contenu ISO complet est servi via HTTP
    pour que cloud-init autoinstall puisse accéder aux paquets.
    """
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"

    kernel_names: list[str] = rule.get("kernel") or ["vmlinuz"]
    initrd_names: list[str] = rule.get("initrd") or ["initrd"]

    # Kernel : chercher vmlinuz, priorité à casper/vmlinuz
    kernel = _find_in_dest(dest, kernel_names, mode="kernel")
    if kernel:
        rel = kernel.relative_to(dest)
        result["kernel_path"] = f"{base}/{rel.as_posix()}"
        logger.info("Ubuntu kernel : %s", rel)
    else:
        logger.warning("Ubuntu kernel non trouvé dans l'ISO")

    # Initrd : chercher initrd / initrd.gz / initrd.lz, priorité à casper/
    initrd = _find_in_dest(dest, initrd_names, mode="initrd")
    if initrd:
        rel = initrd.relative_to(dest)
        result["initrd_path"] = f"{base}/{rel.as_posix()}"
        logger.info("Ubuntu initrd : %s", rel)
    else:
        logger.warning("Ubuntu initrd non trouvé dans l'ISO")

    if not result:
        raise ExtractionError(
            "Aucun fichier de boot Ubuntu (vmlinuz / initrd) trouvé dans l'ISO."
        )
    logger.info("Extraction Ubuntu complète — kernel=%s initrd=%s",
                result.get("kernel_path"), result.get("initrd_path"))
    return result


def _find_in_dest(dest: Path, names: list[str], mode: str = "extra") -> Path | None:
    """
    Comme _find_by_priority mais opère dans un dossier déjà extrait (dest).
    Favorise les fichiers dans casper/ ou isolinux/ (chemins Ubuntu typiques).
    """
    PREFERRED_DIRS = {"casper", "isolinux", "install", "boot"}
    for name in names:
        n_lower = name.lower()
        if mode == "kernel":
            candidates = [
                f for f in dest.rglob("*")
                if f.is_file() and (
                    f.name.lower() == n_lower
                    or f.name.lower().startswith(n_lower + "-")
                )
            ]
        elif mode == "initrd":
            candidates = [
                f for f in dest.rglob("*")
                if f.is_file()
                and (
                    f.name.lower() == n_lower
                    or f.name.lower().startswith(n_lower.split(".")[0] + "-")
                )
                and f.suffix.lower() in INITRD_EXTENSIONS
            ]
        else:
            candidates = [
                f for f in dest.rglob("*")
                if f.is_file() and f.name.lower() == n_lower
            ]

        if candidates:
            # Priorité aux répertoires connus d'Ubuntu
            preferred = [f for f in candidates if f.parent.name.lower() in PREFERRED_DIRS]
            pool = preferred if preferred else candidates
            return max(pool, key=lambda f: f.stat().st_size)
    return None


# ── Recherche par priorité ─────────────────────────────────────────────────────

def _find_by_priority(root: Path, names: list[str], mode: str = "extra") -> Path | None:
    """
    Cherche récursivement les fichiers dont le nom (lowercase) correspond à
    l'une des entrées de `names` (exact ou préfixe si l'entrée finit par '*').
    Retourne le premier match par ordre de priorité, le plus gros en cas d'égalité.
    """
    for name in names:
        if name.endswith("*"):
            prefix = name[:-1].lower()
            candidates = [
                f for f in root.rglob("*")
                if f.is_file() and f.name.lower().startswith(prefix)
            ]
        else:
            n_lower = name.lower()
            if mode == "kernel":
                # Pour le kernel, accepter aussi les noms versionnés : vmlinuz-6.1.0-amd64
                candidates = [
                    f for f in root.rglob("*")
                    if f.is_file() and (
                        f.name.lower() == n_lower
                        or f.name.lower().startswith(n_lower + "-")
                    )
                ]
            elif mode == "initrd":
                candidates = [
                    f for f in root.rglob("*")
                    if f.is_file()
                    and (
                        f.name.lower() == n_lower
                        or f.name.lower().startswith(n_lower.split(".")[0] + "-")
                    )
                    and f.suffix.lower() in INITRD_EXTENSIONS
                ]
            else:
                candidates = [
                    f for f in root.rglob("*")
                    if f.is_file() and f.name.lower() == n_lower
                ]

        if candidates:
            return max(candidates, key=lambda f: f.stat().st_size)

    return None


# ── Permissions ────────────────────────────────────────────────────────────────

def _fix_permissions(path: Path):
    """
    Rend tous les fichiers lisibles par Nginx (www-data).
    Dossiers → 755, fichiers → 644.
    7z conserve parfois les permissions ISO (souvent 400/500) qui bloquent Nginx.
    """
    try:
        for p in path.rglob("*"):
            if p.is_dir():
                p.chmod(0o755)
            else:
                p.chmod(0o644)
        path.chmod(0o755)
        logger.info("Permissions corrigées sur %s", path)
    except Exception as exc:
        logger.warning("Impossible de corriger les permissions sur %s : %s", path, exc)


# ── Nettoyage ──────────────────────────────────────────────────────────────────

def cleanup_boot_files(os_slug: str, version_label: str, version_id: int = 0):
    from app.services.slugify import slugify
    version_slug = slugify(version_label) if version_label else str(version_id)
    dest = settings.boot_dir / os_slug / version_slug
    if dest.exists():
        shutil.rmtree(dest)
        logger.info("Dossier supprimé : %s", dest)
