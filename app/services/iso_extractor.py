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
    # CentOS / Fedora — vmlinuz + initrd.img (extraction partielle)
    "centos": {
        "type":    "linux",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    # Rocky Linux — extraction complète (Anaconda : inst.repo= en génération de menus)
    "rocky": {
        "type":    "rocky",
        "kernel":  ["vmlinuz"],
        "initrd":  ["initrd.img"],
        "extra":   {},
    },
    # AlmaLinux — même schéma EL que Rocky (BaseOS, Appstream, images/pxeboot, .treeinfo)
    "alma": {
        "type":    "alma",
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
    "kernel": ["vmlinuz", "vmlinux", "linux26", "kernel", "bzimage"],
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

def extract_iso(
    iso_path: str,
    os_slug: str,
    version_id: int,
    version_label: str = "",
    os_type=None,
) -> dict:
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

    if os_type is not None:
        from app.services.os_type_extract_plan import try_extract_with_plan

        planned = try_extract_with_plan(iso_path, os_type, version_id, version_label)
        if planned is not None:
            logger.info("Extraction terminée : %s", planned)
            return planned

    rule = DISTRO_RULES.get(os_slug, _GENERIC_RULE)

    if rule["type"] == "skip":
        raise ExtractionError(
            f"L'extraction automatique n'est pas supportée pour {os_slug}. "
            "Uploader les fichiers manuellement."
        )

    if rule["type"] == "esxi":
        logger.info("Extraction complète ESXi (ISO entière sous http) → %s", dest)
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
        paths = _extract_esxi_from_full_dest(dest, os_slug, version_slug)
        logger.info("Extraction terminée : %s", paths)
        return paths

    if rule["type"] in ("windows", "ubuntu", "rocky", "alma"):
        # Extraction COMPLÈTE de l'ISO directement dans dest
        # Windows : tous les fichiers nécessaires pour setup.exe via Samba/HTTP
        # Ubuntu  : contenu ISO nécessaire pour cloud-init autoinstall via HTTP
        # Rocky / Alma : arbre DVD (BaseOS, AppStream, images/, .treeinfo) pour Anaconda (inst.repo)
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
        elif rule["type"] == "ubuntu":
            paths = _find_ubuntu_in_dest(dest, os_slug, version_slug, rule)
        else:
            paths = _find_el_anaconda_iso_in_dest(dest, os_slug, version_slug, rule)
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
    efi_profile: bool,
) -> Path | None:
    """Résout un chemin mentionné dans boot.cfg vers un fichier sur l'ISO extraite."""
    ref = _esxi_normalize_path(ref)
    if not ref:
        return None
    pref = prefix.strip().replace("\\", "/").strip("/")
    rel = ref.lstrip("/")
    candidates: list[Path] = []
    # Références relatives au répertoire du boot.cfg (layout VMware courant sous EFI/BOOT/)
    candidates.append(boot_cfg_dir / PurePosixPath(rel))
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
        try:
            c.resolve().relative_to(iso_root.resolve())
        except ValueError:
            continue
        if c.is_file():
            return _esxi_prefer_index_disk_path(c, iso_index_by_lower)
    # Fallback : même basename, casse différente / doublons EFI vs Legacy (ISO OEM)
    basename = PurePosixPath(rel).name
    if basename and iso_index_by_lower:
        pool = iso_index_by_lower.get(basename.lower(), [])
        if pool:
            return _esxi_disambiguate_same_basename_pool(
                pool,
                iso_root,
                boot_cfg_dir,
                prefix,
                efi_profile=efi_profile,
            )
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


def _esxi_path_under_tree(path: Path, root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _esxi_disambiguate_same_basename_pool(
    pool: list[Path],
    iso_root: Path,
    boot_cfg_dir: Path,
    prefix: str,
    *,
    efi_profile: bool,
) -> Path:
    """
    Plusieurs fichiers peuvent partager le même nom sur une ISO (copie sous ``EFI/BOOT/``, racine,
    bundles OEM). VMware sépare chargeur et ``boot.cfg`` : UEFI utilise ``efi/boot/boot.cfg`` avec
    mboot EFI ; BIOS utilise souvent ``boot.cfg`` à la racine avec mboot.c32 (voir documentation
    Broadcom « About the boot.cfg File » — ``prefix=`` pour kernel/modules).

    On rattache la résolution au répertoire du ``boot.cfg`` source et au bit EFI vs Legacy pour ne
    pas charger les modules de l'autre profil lorsque le fallback « même basename » est utilisé.
    """
    if len(pool) == 1:
        return pool[0]

    iso_r = iso_root.resolve()
    cfg_r = boot_cfg_dir.resolve()
    efi_root: Path | None = None
    try:
        for cand in iso_r.iterdir():
            if cand.is_dir() and cand.name.casefold() == "efi":
                efi_root = cand.resolve()
                break
    except OSError:
        pass

    cfg_in_efi = (
        _esxi_path_under_tree(cfg_r, efi_root)
        if efi_root is not None and efi_root.is_dir()
        else False
    )

    def under_efi_branch(p: Path) -> bool:
        if efi_root is None or not efi_root.is_dir():
            return False
        return _esxi_path_under_tree(p, efi_root)

    narrowed = [p for p in pool if _esxi_path_under_tree(p, cfg_r)]
    if narrowed:
        pool = narrowed

    pref = prefix.strip().replace("\\", "/").strip("/")
    if pref:
        pref_root = (iso_r / pref).resolve()
        if pref_root.is_dir():
            ph = [p for p in pool if _esxi_path_under_tree(p, pref_root)]
            if ph:
                pool = ph

    under = [p for p in pool if under_efi_branch(p)]
    not_under = [p for p in pool if not under_efi_branch(p)]

    if cfg_in_efi and efi_profile:
        if under:
            pool = under
    elif not cfg_in_efi and not efi_profile:
        if not_under:
            pool = not_under
        elif under:
            pool = under
    elif efi_profile and under:
        pool = under
    elif not efi_profile and not_under:
        pool = not_under

    chosen = _esxi_pick_preferred_path(pool, iso_root)
    if len(pool) > 1:
        logger.info(
            "ESXi : fichier même nom — profil %s, boot.cfg « %s » → « %s »",
            "EFI" if efi_profile else "Legacy",
            boot_cfg_dir.relative_to(iso_root),
            chosen.relative_to(iso_root),
        )
    return chosen


def _esxi_prefer_index_disk_path(
    found: Path,
    iso_index_by_lower: dict[str, list[Path]] | None,
) -> Path:
    """
    Quand ``found`` vient du boot.cfg VMware (casse ISO9660 / UEFI souvent en majuscules),
    le même fichier peut être résolu via des candidats construits depuis ces références alors
    que le parcours disque exposé par ``rglob`` reflète la casse réelle des entrées.

    Si un fichier du même inode existe dans l’index ``iso_index_by_lower``, retourne ce Path ;
    sinon ``found``.
    """
    if not iso_index_by_lower:
        return found
    pool = iso_index_by_lower.get(found.name.lower(), [])
    if not pool:
        return found
    for q in pool:
        try:
            if q.samefile(found):
                return q
        except OSError:
            continue
    return found


def _path_has_efi_segment(path: Path, iso_root: Path) -> bool:
    try:
        rel = path.relative_to(iso_root)
    except ValueError:
        return False
    return any(part.lower() == "efi" for part in rel.parts)


def _esxi_pick_mboot_c32_legacy(iso_root: Path, iso_lower: dict[str, list[Path]]) -> Path:
    """Préfère ``mboot.c32`` hors de la branche EFI (chargeur BIOS / chaîne Legacy).

    Certaines ISO dupliquent ``mboot.c32`` sous ``efi/…`` et à la racine : le menu Legacy doit
    pointer vers la copie alignée avec ``ipxe-boot-legacy.cfg``, pas le variant EFI.
    """
    pool = iso_lower.get("mboot.c32", [])
    if not pool:
        raise ExtractionError("ESXi : mboot.c32 introuvable dans l'ISO.")
    outside = [p for p in pool if not _path_has_efi_segment(p, iso_root)]
    chosen_pool = outside if outside else pool
    picked = _esxi_pick_preferred_path(chosen_pool, iso_root)
    if not picked.is_file():
        raise ExtractionError("ESXi : mboot.c32 introuvable.")
    return picked


def _pick_best_esxi_boot_cfg_from_candidates(iso_root: Path, candidates: list[Path]) -> Path | None:
    """Choisit le boot.cfg VMware le plus pertinent (score VMware + préférence installateur).

    Plusieurs ``boot.cfg`` sous EFI peuvent coexister ; un fichier minimal ou hors ``EFI/BOOT``
    peut avoir le même score ``kernel/modules`` mais une liste de modules incomplète ou dans
    un ordre incompatible — ce qui mène à des PSOD du type « Unexpected early boot module … ».
    On préfère donc ``EFI/BOOT/boot.cfg`` puis les chemins sous ``EFI/BOOT/``.
    Hors EFI : préfère ``boot.cfg`` à la racine puis ``…/boot/boot.cfg``.
    """
    if not candidates:
        return None
    best: tuple[tuple[int, int, int, str], Path] | None = None
    for p in candidates:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rank = _esxi_boot_cfg_rank(iso_root, p, txt)
        if rank[0] == 0:
            continue
        if best is None or rank > best[0]:
            best = (rank, p)
    return best[1] if best else None


def _esxi_boot_cfg_rank(iso_root: Path, p: Path, txt: str) -> tuple[int, int, int, str]:
    """Clé de tri décroissante : score contenu, nombre de modules parsés, tier installateur."""
    low = txt.lower()
    score = 0
    if "kernel=" in low:
        score += 2
    if "modules=" in low or re.search(r"(?m)^module\s*=", txt):
        score += 2
    if "kernelopt=" in low:
        score += 1
    _, mod_list = _parse_esxi_boot_cfg_text(txt)
    mod_count = len(mod_list)
    try:
        rel = p.relative_to(iso_root).as_posix().lower().strip("/")
    except ValueError:
        rel = ""
    parts = rel.split("/") if rel else []
    tier = 0
    if len(parts) >= 3 and parts[0] == "efi" and parts[1] == "boot" and parts[-1] == "boot.cfg":
        tier = 100
    elif len(parts) >= 2 and parts[0] == "efi" and parts[1] == "boot":
        tier = 50
    elif "efi" in parts:
        tier = 10
    elif rel == "boot.cfg":
        tier = 80
    elif len(parts) >= 2 and parts[-1] == "boot.cfg" and parts[-2] == "boot":
        tier = 60
    return (score, mod_count, tier, rel)


def _pick_esxi_boot_cfg(iso_root: Path, idx: dict[str, list[Path]]) -> Path | None:
    """Choisit un boot.cfg pertinent sur l'ISO (tous emplacements)."""
    pool = idx.get("boot.cfg", [])
    if not pool:
        return None
    return _pick_best_esxi_boot_cfg_from_candidates(iso_root, list(pool))


def _pick_esxi_boot_cfg_efi(iso_root: Path, idx: dict[str, list[Path]]) -> Path | None:
    """Préfère EFI/…/boot.cfg pour le profil installateur UEFI."""
    pool = [p for p in idx.get("boot.cfg", []) if _path_has_efi_segment(p, iso_root)]
    if not pool:
        return _pick_esxi_boot_cfg(iso_root, idx)
    return _pick_best_esxi_boot_cfg_from_candidates(iso_root, pool)


def _pick_esxi_boot_cfg_legacy(iso_root: Path, idx: dict[str, list[Path]]) -> Path | None:
    """Préfère ``boot.cfg`` VMware BIOS : racine ISO puis ``boot/boot.cfg`` (voir doc installateur ESXi)."""
    pool = [p for p in idx.get("boot.cfg", []) if not _path_has_efi_segment(p, iso_root)]
    if not pool:
        return _pick_esxi_boot_cfg(iso_root, idx)

    def legacy_site_rank(p: Path) -> tuple[int, str]:
        try:
            rel = p.relative_to(iso_root).as_posix().replace("\\", "/").lower().strip("/")
        except ValueError:
            return (99, "")
        if rel == "boot.cfg":
            return (0, rel)
        if rel == "boot/boot.cfg":
            return (1, rel)
        return (10, rel)

    best_site = min(legacy_site_rank(p)[0] for p in pool)
    pool = [p for p in pool if legacy_site_rank(p)[0] == best_site]
    return _pick_best_esxi_boot_cfg_from_candidates(iso_root, pool)


def _rewrite_esxi_boot_cfg_http(
    raw_cfg: str,
    http_prefix: str,
    kernel_rel: str,
    module_rels: list[str],
) -> str:
    """
    Réécrit ``boot.cfg`` pour HTTP en **reprenant la structure du fichier VMware source**
    (ordre des lignes, métadonnées ``bootstate`` / ``timeout`` / ``build`` / …, lignes
    ``module=`` séparées ou une ligne ``modules= … --- …`` comme dans le fichier d’origine).

    ``prefix`` est fixé à l’URL HTTP de la racine version ; ``kernel`` / ``modules`` /
    ``module`` utilisent les chemins relatifs **tels que sur le disque après extraction 7z**
    (même casse que l’ISO VMware) ; ``kernelopt`` est repris sans ``cdromBoot``.
    """
    merged_body = _esxi_merge_cfg_continuations(raw_cfg)
    http_prefix = http_prefix.rstrip("/") + "/"

    lines_out: list[str] = [
        "# iPXE Manager — ISO ESXi extraite en entier ; prefix = racine HTTP de cette version.",
    ]

    qi = 0
    wrote_prefix = False

    for raw_line in merged_body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            lines_out.append(stripped)
            continue
        if "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        kl = key.strip().lower()
        val_s = val.strip()

        if kl == "prefix":
            lines_out.append(f"prefix={http_prefix}")
            wrote_prefix = True
            continue
        if kl == "kernel":
            lines_out.append(f"kernel={kernel_rel}")
            continue
        if kl == "kernelopt":
            kopt = val_s
            if kopt:
                kopt = re.sub(r"\bcdromBoot\b", "", kopt, flags=re.I)
                kopt = re.sub(r"\s+", " ", kopt).strip()
            lines_out.append(f"kernelopt={kopt}" if kopt else "kernelopt=")
            continue
        if kl == "modules":
            parts = [p.strip() for p in _MODULES_SPLIT_RE.split(val_s) if p.strip()]
            n = len(parts)
            chunk = module_rels[qi : qi + n]
            if len(chunk) != n:
                raise ExtractionError(
                    "ESXi boot.cfg HTTP : incohérence « modules= » / fichiers résolus "
                    f"(ligne prévoit {n} segment(s), il en reste {len(module_rels) - qi} à mapper)."
                )
            qi += n
            lines_out.append("modules=" + " --- ".join(chunk))
            continue
        if kl == "module":
            if not val_s:
                lines_out.append(stripped)
                continue
            if qi >= len(module_rels):
                raise ExtractionError(
                    "ESXi boot.cfg HTTP : ligne « module= » en trop pour les fichiers résolus."
                )
            lines_out.append(f"module={module_rels[qi]}")
            qi += 1
            continue

        lines_out.append(stripped)

    if not wrote_prefix:
        lines_out.insert(1, f"prefix={http_prefix}")

    if qi != len(module_rels):
        raise ExtractionError(
            "ESXi boot.cfg HTTP : "
            f"{len(module_rels) - qi} fichier(s) résolu(s) non référencé(s) dans le boot.cfg source."
        )

    return "\n".join(lines_out) + "\n"


def _esxi_rel_from_dest(dest: Path, file_path: Path) -> str:
    """
    Chemin relatif POSIX depuis ``dest`` vers ``file_path``, **tel que nommé sur le disque**
    après extraction (représente fidèlement l’arborescence ISO / UDF).
    """
    dest_r = dest.resolve()
    fp = file_path if file_path.is_absolute() else (dest_r / file_path)
    target_r = fp.resolve()
    try:
        rel = target_r.relative_to(dest_r)
    except ValueError as exc:
        raise ExtractionError(
            f"ESXi : fichier « {fp} » en dehors de la racine version « {dest_r} »."
        ) from exc

    cur = dest_r
    parts_out: list[str] = []
    for seg in rel.parts:
        try:
            entries = list(cur.iterdir())
        except OSError as exc:
            logger.warning(
                "ESXi : lecture répertoire %s impossible (%s) — suffixe brut depuis résolution.",
                cur,
                exc,
            )
            si = len(parts_out)
            return PurePosixPath(*(parts_out + list(rel.parts[si:]))).as_posix()
        chosen: Path | None = None
        for ch in entries:
            if ch.name.casefold() == seg.casefold():
                chosen = ch
                break
        if chosen is None:
            logger.warning(
                "ESXi : segment « %s » introuvable sous %s — suffixe brut depuis boot.cfg.",
                seg,
                cur,
            )
            si = len(parts_out)
            return PurePosixPath(*(parts_out + list(rel.parts[si:]))).as_posix()
        parts_out.append(chosen.name)
        cur = chosen
    return PurePosixPath(*parts_out).as_posix()


def _esxi_boot_cfg_http_payload(
    dest: Path,
    iso_lower: dict[str, list[Path]],
    src_boot_cfg: Path,
    http_prefix: str,
    *,
    profile_label: str,
) -> tuple[str, list[str]]:
    """
    Lit un boot.cfg VMware source et produit le corps HTTP (ipxe-boot*.cfg)
    + liste ordonnée des chemins relatifs pour préchargement iPXE.

    Les chemins ``kernel=`` et ``modules=`` sont dérivés des ``Path`` résolus puis de
    ``_esxi_rel_from_dest`` — alignés sur les fichiers réels extraits de l’ISO.
    """
    raw_cfg = src_boot_cfg.read_text(encoding="utf-8", errors="replace")
    parsed, mod_refs = _parse_esxi_boot_cfg_text(raw_cfg)
    cfg_dir = src_boot_cfg.parent
    old_prefix = (parsed.get("prefix") or "").strip()
    efi_profile = _path_has_efi_segment(src_boot_cfg, dest)

    kernel_ref = (parsed.get("kernel") or "").strip()
    if not kernel_ref:
        raise ExtractionError(
            f"ESXi ({profile_label}) : pas de ligne kernel= dans « {src_boot_cfg.relative_to(dest)} »."
        )

    if not mod_refs:
        raise ExtractionError(
            f"ESXi ({profile_label}) : aucun module (modules= / module=) dans « {src_boot_cfg.relative_to(dest)} »."
        )

    k_path = _esxi_resolve_file(
        dest,
        cfg_dir,
        old_prefix,
        kernel_ref,
        iso_index_by_lower=iso_lower,
        efi_profile=efi_profile,
    )
    if not k_path or not k_path.is_file():
        raise ExtractionError(
            f"ESXi ({profile_label}) : fichier kernel « {kernel_ref} » introuvable "
            f"(boot.cfg « {src_boot_cfg.relative_to(dest)} »)."
        )

    mod_paths: list[Path] = []
    for ref in mod_refs:
        p = _esxi_resolve_file(
            dest,
            cfg_dir,
            old_prefix,
            ref,
            iso_index_by_lower=iso_lower,
            efi_profile=efi_profile,
        )
        if not p or not p.is_file():
            raise ExtractionError(f"ESXi ({profile_label}) : module « {ref} » introuvable.")
        mod_paths.append(p)

    kernel_rel = _esxi_rel_from_dest(dest, k_path)
    mod_rels = [_esxi_rel_from_dest(dest, p) for p in mod_paths]

    managed = _rewrite_esxi_boot_cfg_http(
        raw_cfg=raw_cfg,
        http_prefix=http_prefix,
        kernel_rel=kernel_rel,
        module_rels=mod_rels,
    )

    preload_rels: list[str] = []
    seen_low: set[str] = set()

    def add_rel(rp: str) -> None:
        low = rp.lower()
        if low not in seen_low:
            seen_low.add(low)
            preload_rels.append(rp)

    add_rel(kernel_rel)
    for p in mod_paths:
        add_rel(_esxi_rel_from_dest(dest, p))

    return managed, preload_rels


def _extract_esxi_from_full_dest(dest: Path, os_slug: str, version_slug: str) -> dict:
    """
    Après extraction 7z complète dans ``dest`` :
    - L’arborescence est **celle de l’ISO** (aucun renommage de casse — évite collisions et boucles).
    - ``ipxe-boot.cfg`` / ``ipxe-boot-legacy.cfg`` et les JSON reprennent les chemins **tels que sur disque**.
    - ``ipxe-boot.cfg`` : ``boot.cfg`` du **profil UEFI** (souvent ``EFI/BOOT/boot.cfg``, mboot EFI).
    - ``ipxe-boot-legacy.cfg`` : ``boot.cfg`` **BIOS** (souvent ``boot.cfg`` à la racine ou ``boot/boot.cfg``, mboot.c32).
    - Chemins kernel/modules résolus séparément par profil pour éviter les fichiers homonymes sous ``EFI/`` vs racine.

    Référence : VMware « About the boot.cfg File » — ``prefix=``, chemins kernel/modules, chargeurs mboot.c32 / EFI.
    """
    base = f"boot/{os_slug}/{version_slug}"
    iso_lower = _esxi_index_files_casefold(dest)

    src_efi_cfg = _pick_esxi_boot_cfg_efi(dest, iso_lower)
    if not src_efi_cfg:
        raise ExtractionError("ESXi : aucun boot.cfg trouvé après extraction complète de l'ISO.")

    src_legacy_cfg = _pick_esxi_boot_cfg_legacy(dest, iso_lower)
    if not src_legacy_cfg:
        src_legacy_cfg = src_efi_cfg

    mboot_path = _esxi_pick_mboot_c32_legacy(dest, iso_lower)

    http_prefix = settings.server_base_url.rstrip("/") + f"/{base}/"

    managed_efi, preload_efi = _esxi_boot_cfg_http_payload(
        dest, iso_lower, src_efi_cfg, http_prefix, profile_label="EFI"
    )
    (dest / "ipxe-boot.cfg").write_text(managed_efi, encoding="utf-8")
    logger.info(
        "ESXi : ipxe-boot.cfg — source %s",
        src_efi_cfg.relative_to(dest),
    )

    if src_legacy_cfg.resolve() == src_efi_cfg.resolve():
        managed_legacy = managed_efi
        preload_legacy = list(preload_efi)
        logger.info("ESXi : même boot.cfg EFI/Legacy — ipxe-boot-legacy.cfg identique.")
    else:
        managed_legacy, preload_legacy = _esxi_boot_cfg_http_payload(
            dest, iso_lower, src_legacy_cfg, http_prefix, profile_label="Legacy"
        )
    (dest / "ipxe-boot-legacy.cfg").write_text(managed_legacy, encoding="utf-8")
    logger.info(
        "ESXi : ipxe-boot-legacy.cfg — source %s",
        src_legacy_cfg.relative_to(dest),
    )

    modules_json = json.dumps(preload_efi, separators=(",", ":"))
    modules_legacy_json = json.dumps(preload_legacy, separators=(",", ":"))

    mboot_rel = _esxi_rel_from_dest(dest, mboot_path)

    efi_boot_pool = iso_lower.get("bootx64.efi", [])
    esxi_efi_boot_http: str | None = None
    if efi_boot_pool:
        efi_chosen = _esxi_pick_preferred_path(efi_boot_pool, dest)
        if efi_chosen.is_file():
            mboot_dest = efi_chosen.parent / "mboot.efi"
            shutil.copy2(efi_chosen, mboot_dest)
            esxi_efi_boot_http = f"{base}/{_esxi_rel_from_dest(dest, mboot_dest)}"
            logger.info(
                "ESXi : mboot.efi (copie bootx64.efi) — %s",
                _esxi_rel_from_dest(dest, mboot_dest),
            )
        else:
            logger.warning("ESXi : bootx64.efi indexé mais fichier illisible — UEFI menu désactivé.")
    else:
        logger.warning("ESXi : bootx64.efi introuvable sur l'ISO — entrée menu UEFI absente.")

    logger.info(
        "ESXi extraction complète — mboot=%s modules_iPXE EFI=%d Legacy=%d",
        mboot_rel,
        len(preload_efi),
        len(preload_legacy),
    )

    out_paths: dict = {
        "kernel_path": f"{base}/{mboot_rel}",
        "esxi_boot_cfg_path": f"{base}/ipxe-boot.cfg",
        "esxi_boot_cfg_legacy_path": f"{base}/ipxe-boot-legacy.cfg",
        "esxi_modules": modules_json,
        "esxi_modules_legacy": modules_legacy_json,
    }
    if esxi_efi_boot_http:
        out_paths["esxi_efi_boot_path"] = esxi_efi_boot_http
    return out_paths


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


def _find_el_anaconda_iso_in_dest(dest: Path, os_slug: str, version_slug: str, rule: dict) -> dict:
    """
    Après extraction complète d'une ISO EL (Rocky, AlmaLinux, …) dans ``dest``,
    localise vmlinuz + initrd.img dans ``images/pxeboot/`` (ou équivalent).
    Le reste de l'arbre (BaseOS, Appstream, .treeinfo, images/install.img) reste servi via HTTP.
    """
    label = os_slug.upper()
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"
    kernel_names: list[str] = rule.get("kernel") or ["vmlinuz"]
    initrd_names: list[str] = rule.get("initrd") or ["initrd.img"]
    rhel_boot_pref = frozenset({"pxeboot", "images", "efi", "boot", "isolinux"})

    kernel = _find_in_dest(
        dest, kernel_names, mode="kernel", preferred_parent_names=rhel_boot_pref
    )
    if kernel:
        rel = kernel.relative_to(dest)
        result["kernel_path"] = f"{base}/{rel.as_posix()}"
        logger.info("%s kernel : %s", label, rel)
    else:
        logger.warning("%s : kernel non trouvé dans l'ISO (cherché vmlinuz)", label)

    initrd = _find_in_dest(
        dest, initrd_names, mode="initrd", preferred_parent_names=rhel_boot_pref
    )
    if initrd:
        rel = initrd.relative_to(dest)
        result["initrd_path"] = f"{base}/{rel.as_posix()}"
        logger.info("%s initrd : %s", label, rel)
    else:
        logger.warning("%s : initrd non trouvé (cherché initrd.img)", label)

    if not result:
        raise ExtractionError(
            f"Aucun fichier de boot {os_slug} (vmlinuz / initrd.img) trouvé dans l'ISO."
        )
    logger.info(
        "Extraction %s complète — kernel=%s initrd=%s",
        os_slug,
        result.get("kernel_path"),
        result.get("initrd_path"),
    )
    return result


def _find_in_dest(
    dest: Path,
    names: list[str],
    mode: str = "extra",
    *,
    preferred_parent_names: set[str] | frozenset[str] | None = None,
) -> Path | None:
    """
    Comme _find_by_priority mais opère dans un dossier déjà extrait (dest).
    Par défaut favorise casper/ / isolinux/ (Ubuntu). Passer ``preferred_parent_names`` pour
    d'autres layouts (ex. pxeboot/ pour Rocky / AlmaLinux EL).
    """
    if preferred_parent_names is not None:
        PREFERRED_DIRS = {x.lower() for x in preferred_parent_names}
    else:
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
            # Priorité aux répertoires connus (Ubuntu : casper… / Rocky : pxeboot…)
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
