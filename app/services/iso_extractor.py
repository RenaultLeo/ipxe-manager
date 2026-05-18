"""
Extracteur ISO par distribution.
Utilise 7z pour extraire l'ISO, puis cherche les fichiers de boot
avec des règles spécifiques à chaque distro.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import shutil
import tempfile
import re
from collections.abc import Iterable
from pathlib import PurePosixPath, Path
from urllib.parse import unquote, urlparse, urlunparse

from app.config import settings

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


# Initrd Anaconda / netboot (EL, Fedora) : plusieurs noms selon l’ISO (Live, ISO anciennes).
_ANACONDA_INITRD_CANDIDATES = ("initrd.img", "initrd0.img", "initrd")


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
    # CentOS — extraction complète EL (Anaconda : inst.repo= comme Rocky / Alma ; isolinux ou images/pxeboot)
    "centos": {
        "type":    "centos",
        "kernel":  ["vmlinuz"],
        "initrd":  list(_ANACONDA_INITRD_CANDIDATES),
        "extra":   {},
    },
    # Rocky Linux — extraction complète (Anaconda : inst.repo= en génération de menus)
    "rocky": {
        "type":    "rocky",
        "kernel":  ["vmlinuz"],
        "initrd":  list(_ANACONDA_INITRD_CANDIDATES),
        "extra":   {},
    },
    # AlmaLinux — même schéma EL que Rocky (BaseOS, Appstream, images/pxeboot, .treeinfo)
    "alma": {
        "type":    "alma",
        "kernel":  ["vmlinuz"],
        "initrd":  list(_ANACONDA_INITRD_CANDIDATES),
        "extra":   {},
    },
    "fedora": {
        "type":    "fedora",
        "kernel":  ["vmlinuz"],
        "initrd":  list(_ANACONDA_INITRD_CANDIDATES),
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

    if rule["type"] in ("windows", "ubuntu", "rocky", "alma", "centos", "fedora"):
        # Extraction COMPLÈTE de l'ISO directement dans dest
        # Windows : tous les fichiers nécessaires pour setup.exe via Samba/HTTP
        # Ubuntu  : contenu ISO nécessaire pour cloud-init autoinstall via HTTP
        # Rocky / Alma / CentOS / Fedora : arbre DVD pour Anaconda (inst.repo)
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


# ── VMware ESXi : extraction 7z complète + boot.cfg HTTP ────────────────────────

_MODULES_SPLIT_RE = re.compile(r"\s*---\s*")
# VMware / OEM : ``KERNEL=``, espaces autour de ``=``, BOM → détection tolérante pour tout passer en minuscules.
_RE_ESXI_LINE_KERNEL = re.compile(r"^kernel\s*=\s*(.*)$", re.I)
_RE_ESXI_LINE_MODULES = re.compile(r"^modules\s*=\s*(.*)$", re.I)
_RE_ESXI_LINE_MODULE = re.compile(r"^module\s*=\s*(.*)$", re.I)
_RE_ESXI_LINE_PREFIX = re.compile(r"^prefix(?:-http)?\s*=", re.I)
_RE_ESXI_LINE_KERNELOPT = re.compile(r"^kernelopt\s*=", re.I)


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
    """Résout une entrée kernel/module VMware vers un fichier sous ``iso_root`` (extract 7z tel quel)."""
    ref = _esxi_normalize_path(ref)
    if not ref:
        return None
    pref = prefix.strip().replace("\\", "/").strip("/")
    rel = ref.lstrip("/")
    candidates: list[Path] = []
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
            return c
    basename = PurePosixPath(rel).name
    if basename and iso_index_by_lower:
        pool = iso_index_by_lower.get(basename.lower(), [])
        if pool:
            return _esxi_pick_preferred_path(pool, iso_root)
    return None


def _esxi_index_files_casefold(iso_root: Path) -> dict[str, list[Path]]:
    """Index tous les fichiers par nom en bas de casse (recherche ``mboot.c32``, ``boot.cfg``, …)."""
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


def _esxi_ensure_crypto64_lowercase_http_alias(
    iso_lower: dict[str, list[Path]],
    iso_root: Path,
) -> None:
    """mboot demande souvent ``prefix/crypto64.efi`` en minuscules alors que l’ISO a ``CRYPTO64.EFI``.

    - lien ``crypto64.efi`` dans le même répertoire que le binaire VMware ;
    - lien à la **racine** du dépôt HTTP (``prefix/crypto64.efi``), pointant vers le même fichier.
    """
    pool = iso_lower.get("crypto64.efi")
    if not pool:
        return
    src = _esxi_pick_preferred_path(pool, iso_root).resolve()
    if not src.is_file():
        return

    if src.name != "crypto64.efi":
        alias = src.parent / "crypto64.efi"
        if not alias.is_file():
            try:
                os.link(src, alias)
                logger.info("ESXi : crypto64.efi → lien dur dans %s", src.parent)
            except OSError:
                try:
                    shutil.copy2(src, alias)
                    logger.info("ESXi : crypto64.efi → copie dans %s", src.parent)
                except OSError as exc:
                    logger.warning("ESXi : crypto64.efi dans dossier VMware impossible (%s)", exc)

    root_alias = (iso_root / "crypto64.efi").resolve()
    try:
        if root_alias.samefile(src):
            return
    except OSError:
        pass
    if root_alias.is_file():
        try:
            if root_alias.samefile(src):
                return
        except OSError:
            pass
        logger.warning(
            "ESXi : « crypto64.efi » existe déjà à la racine HTTP et ne pointe pas vers le crypto VMware — ignoré.",
        )
        return
    try:
        os.link(src, root_alias)
        logger.info("ESXi : crypto64.efi lien à la racine HTTP")
    except OSError:
        try:
            shutil.copy2(src, root_alias)
            logger.info("ESXi : crypto64.efi copie à la racine HTTP")
        except OSError as exc:
            logger.warning("ESXi : crypto64.efi racine HTTP impossible (%s)", exc)


def _esxi_crypto64_path_for_http(crypto_path: Path) -> Path:
    """Pour URLs iPXE / cohérence mboot : préférer le nom ``crypto64.efi`` si même fichier."""
    alias = crypto_path.parent / "crypto64.efi"
    if alias.is_file():
        try:
            if alias.samefile(crypto_path):
                return alias
        except OSError:
            pass
    return crypto_path


def _pick_esxi_boot_cfg_any(iso_root: Path, idx: dict[str, list[Path]]) -> Path | None:
    """Le ``boot.cfg`` le moins profond si plusieurs (cas ISO atypiques)."""
    pool = idx.get("boot.cfg", [])
    if not pool:
        return None
    return _esxi_pick_preferred_path(pool, iso_root)


def _esxi_lowercase_http_url_path(url: str) -> str:
    """Segments du chemin d’une URL HTTP(S) en minuscules (hôte/query inchangés)."""
    try:
        pr = urlparse(url)
        path = unquote(pr.path or "")
        if "/" not in path:
            return url
        low_segments = [seg.lower() for seg in path.split("/")]
        new_path = "/".join(low_segments)
        return urlunparse(pr._replace(path=new_path))
    except ValueError:
        return url


def _esxi_boot_cfg_lowercase_if_path_like(val: str) -> str:
    """Pour valeurs boot.cfg hors kernel/modules : URL ou chemin avec « / » → segments minuscules."""
    t = val.strip()
    if not t:
        return t
    tl = t.lower()
    if tl.startswith(("http://", "https://")):
        return _esxi_lowercase_http_url_path(t)
    uni = t.replace("\\", "/")
    if "/" in uni:
        # éviter les libellés « foo / bar » (pas un chemin fichier VMware)
        if any(ch.isspace() for ch in uni):
            return t
        return _esxi_lowercase_posix_rel(uni)
    return t


def _esxi_boot_cfg_lowercase_kernelopt_token(tok: str) -> str:
    """Un jeton kernelopt : préserve ``clé=valeur`` ; valeur mise en minuscules si chemin/URL."""
    t = tok.strip()
    if not t:
        return t
    if t.lower().startswith(("http://", "https://")):
        return _esxi_boot_cfg_lowercase_if_path_like(t)
    if "=" in t:
        pk, _, pv = t.partition("=")
        return f"{pk}={_esxi_boot_cfg_lowercase_if_path_like(pv)}"
    return _esxi_boot_cfg_lowercase_if_path_like(t)


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
    ``module`` : chemins relatifs fournis par l’appelant (segments **minuscules**, mboot / nginx).
    Les lignes ``#`` du fichier VMware source sont ignorées pour éviter des mentions obsolètes en majuscules.
    Les autres clés sont recopiées telles quelles (sauf ``prefix`` / ``prefix-http`` → ``prefix`` HTTP iPXE Manager).
    """
    merged_body = _esxi_merge_cfg_continuations(raw_cfg).lstrip("\ufeff")
    http_prefix = http_prefix.rstrip("/") + "/"

    lines_out: list[str] = [
        "# iPXE Manager — ISO ESXi extraite en entier ; prefix = racine HTTP de cette version.",
    ]

    qi = 0
    wrote_prefix = False

    for raw_line in merged_body.splitlines():
        stripped = raw_line.strip().lstrip("\ufeff")
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue

        if _RE_ESXI_LINE_PREFIX.match(stripped):
            lines_out.append(f"prefix={http_prefix}")
            wrote_prefix = True
            continue

        mk = _RE_ESXI_LINE_KERNEL.match(stripped)
        if mk:
            lines_out.append(f"kernel={kernel_rel}")
            continue

        if _RE_ESXI_LINE_KERNELOPT.match(stripped):
            _, _, rest = stripped.partition("=")
            val_s = rest.strip()
            kopt = val_s
            if kopt:
                kopt = re.sub(r"\bcdromBoot\b", "", kopt, flags=re.I)
                kopt = re.sub(r"\s+", " ", kopt).strip()
                kopt = " ".join(_esxi_boot_cfg_lowercase_kernelopt_token(tok) for tok in kopt.split(" "))
            lines_out.append(f"kernelopt={kopt}" if kopt else "kernelopt=")
            continue

        mm = _RE_ESXI_LINE_MODULES.match(stripped)
        if mm:
            val_s = (mm.group(1) or "").strip()
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

        mo = _RE_ESXI_LINE_MODULE.match(stripped)
        if mo:
            val_s = (mo.group(1) or "").strip()
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

        if "=" not in stripped:
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


def _esxi_lowercase_posix_rel(rel: str) -> str:
    """Forme canonique mboot / URLs HTTP VMware : tous les segments du chemin relatif en minuscules."""
    rel = rel.strip().replace("\\", "/").strip("/")
    if not rel:
        return rel
    return "/".join(seg.lower() for seg in rel.split("/"))


def normalize_esxi_ipxe_boot_cfg_paths(text: str) -> str:
    """
    Met en minuscules les segments des chemins dans ``kernel=``, ``modules=`` et ``module=``.
    Répare les ``ipxe-boot.cfg`` déjà déployés lors d'une régénération menus (casse OEM, ``KERNEL=``, espaces) ;
    évite les 404 nginx/ext4 sensible à la casse sans ré-extraire l'ISO.
    """
    lines_out: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip().lstrip("\ufeff")
        if not stripped:
            lines_out.append("")
            continue
        if stripped.startswith("#"):
            lines_out.append(raw_line.rstrip("\r"))
            continue

        mk = _RE_ESXI_LINE_KERNEL.match(stripped)
        if mk:
            val_s = (mk.group(1) or "").strip()
            lines_out.append(f"kernel={_esxi_lowercase_posix_rel(val_s)}")
            continue

        mm = _RE_ESXI_LINE_MODULES.match(stripped)
        if mm:
            val_s = (mm.group(1) or "").strip()
            parts = [p.strip() for p in _MODULES_SPLIT_RE.split(val_s) if p.strip()]
            lines_out.append(
                "modules=" + " --- ".join(_esxi_lowercase_posix_rel(p) for p in parts)
            )
            continue

        mo = _RE_ESXI_LINE_MODULE.match(stripped)
        if mo:
            val_s = (mo.group(1) or "").strip()
            lines_out.append(
                "module=" if not val_s else f"module={_esxi_lowercase_posix_rel(val_s)}"
            )
            continue

        lines_out.append(raw_line.rstrip("\r"))
    return "\n".join(lines_out) + "\n"


def _esxi_ensure_lowercase_http_mirrors(dest: Path, files: Iterable[Path]) -> None:
    """Pour chaque fichier sous ``dest``, garantit un second chemin dont chaque segment est en minuscules,
    lien dur (ou copie) vers le même contenu — nginx/ext4 sensible à la casse."""
    dest_r = dest.resolve()
    seen: set[str] = set()
    for fp in files:
        fp_r = fp.resolve()
        try:
            rel = fp_r.relative_to(dest_r)
        except ValueError:
            continue
        sk = str(fp_r)
        if sk in seen:
            continue
        seen.add(sk)
        mirror = dest_r.joinpath(*[seg.lower() for seg in rel.parts])
        if fp_r == mirror:
            continue
        mirror.parent.mkdir(parents=True, exist_ok=True)
        if mirror.is_file():
            try:
                if mirror.samefile(fp_r):
                    continue
            except OSError:
                pass
            logger.warning("ESXi EFI : collision miroir minuscules « %s » — ignoré.", mirror)
            continue
        try:
            os.link(fp_r, mirror)
        except OSError:
            try:
                shutil.copy2(fp_r, mirror)
            except OSError as exc:
                logger.warning("ESXi EFI : miroir « %s » impossible (%s)", mirror, exc)


def _esxi_rel_from_dest(dest: Path, file_path: Path) -> str:
    """Chemin relatif POSIX depuis ``dest`` vers ``file_path`` (casse identique au disque après 7z)."""
    dest_r = dest.resolve()
    fp = file_path if file_path.is_absolute() else (dest_r / file_path)
    target_r = fp.resolve()
    try:
        return target_r.relative_to(dest_r).as_posix()
    except ValueError as exc:
        raise ExtractionError(
            f"ESXi : fichier « {fp} » en dehors de la racine version « {dest_r} »."
        ) from exc


def _esxi_boot_cfg_http_payload(
    dest: Path,
    iso_lower: dict[str, list[Path]],
    src_boot_cfg: Path,
    http_prefix: str,
    *,
    profile_label: str,
) -> tuple[str, list[str]]:
    """
    Lit un boot.cfg VMware source et produit le corps HTTP ``ipxe-boot.cfg``
    + liste ordonnée des chemins relatifs pour préchargement iPXE (**sans dédoublonnage**, ordre VMware inchangé).

    Les chemins ``kernel=`` / ``modules=`` sont toujours en **minuscules** (HTTP mboot), avec **miroirs** lien dur sur disque.
    ``crypto64.efi`` est préfixé au JSON lorsque ``profile_label`` est **EFI** (mboot.efi).
    """
    raw_cfg = src_boot_cfg.read_text(encoding="utf-8", errors="replace")
    parsed, mod_refs = _parse_esxi_boot_cfg_text(raw_cfg)
    cfg_dir = src_boot_cfg.parent
    old_prefix = (parsed.get("prefix") or "").strip()

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
        )
        if not p or not p.is_file():
            raise ExtractionError(f"ESXi ({profile_label}) : module « {ref} » introuvable.")
        mod_paths.append(p)

    crypto_path_opt: Path | None = None
    if profile_label.casefold() == "efi":
        crypto_pool = iso_lower.get("crypto64.efi")
        if crypto_pool:
            cp = _esxi_crypto64_path_for_http(_esxi_pick_preferred_path(crypto_pool, dest))
            if cp.is_file():
                crypto_path_opt = cp

    mirror_paths = [k_path] + mod_paths
    if crypto_path_opt is not None:
        mirror_paths.append(crypto_path_opt)
    _esxi_ensure_lowercase_http_mirrors(dest, mirror_paths)

    kernel_rel = _esxi_lowercase_posix_rel(_esxi_rel_from_dest(dest, k_path))
    mod_rels = [_esxi_lowercase_posix_rel(_esxi_rel_from_dest(dest, p)) for p in mod_paths]

    managed = _rewrite_esxi_boot_cfg_http(
        raw_cfg=raw_cfg,
        http_prefix=http_prefix,
        kernel_rel=kernel_rel,
        module_rels=mod_rels,
    )

    preload_rels: list[str] = []
    if profile_label.casefold() == "efi":
        if crypto_path_opt is not None:
            try:
                dup = crypto_path_opt.samefile(k_path) or any(
                    crypto_path_opt.samefile(mp) for mp in mod_paths
                )
            except OSError:
                dup = False
            if not dup:
                preload_rels.append(
                    _esxi_lowercase_posix_rel(_esxi_rel_from_dest(dest, crypto_path_opt))
                )
                logger.info(
                    "ESXi (%s) : crypto64.efi en tête du préchargement iPXE (HTTP minuscules).",
                    profile_label,
                )
    preload_rels.append(kernel_rel)
    preload_rels.extend(mod_rels)

    return managed, preload_rels


def _extract_esxi_from_full_dest(dest: Path, os_slug: str, version_slug: str) -> dict:
    """
    Après extraction 7z dans ``dest`` (arborescence ISO inchangée) :
    un seul ``ipxe-boot.cfg`` + JSON ``esxi_modules`` (chemins HTTP minuscules, miroirs lien dur),
    utilisés à la fois par mboot.efi et mboot.c32 ; précharge ``crypto64.efi`` si présent ;
    alias ``crypto64.efi`` racine HTTP conservé.
    """
    base = f"boot/{os_slug}/{version_slug}"
    iso_lower = _esxi_index_files_casefold(dest)
    _esxi_ensure_crypto64_lowercase_http_alias(iso_lower, dest)

    src_cfg = _pick_esxi_boot_cfg_any(dest, iso_lower)
    if not src_cfg:
        raise ExtractionError("ESXi : aucun boot.cfg trouvé après extraction complète de l'ISO.")

    mboot_pool = iso_lower.get("mboot.c32", [])
    if not mboot_pool:
        raise ExtractionError("ESXi : mboot.c32 introuvable dans l'ISO.")
    mboot_path = _esxi_pick_preferred_path(mboot_pool, dest)
    if not mboot_path.is_file():
        raise ExtractionError("ESXi : mboot.c32 introuvable.")
    _esxi_ensure_lowercase_http_mirrors(dest, [mboot_path])

    http_prefix = settings.server_base_url.rstrip("/") + f"/{base}/"

    managed, preload = _esxi_boot_cfg_http_payload(
        dest, iso_lower, src_cfg, http_prefix, profile_label="EFI"
    )
    managed = normalize_esxi_ipxe_boot_cfg_paths(managed)
    (dest / "ipxe-boot.cfg").write_text(managed, encoding="utf-8")
    legacy_stale = dest / "ipxe-boot-legacy.cfg"
    if legacy_stale.is_file():
        try:
            legacy_stale.unlink()
        except OSError as exc:
            logger.warning("ESXi : suppression ipxe-boot-legacy.cfg obsolète impossible (%s)", exc)

    modules_json = json.dumps(preload, separators=(",", ":"))

    mboot_rel = _esxi_lowercase_posix_rel(_esxi_rel_from_dest(dest, mboot_path))

    efi_boot_pool = iso_lower.get("bootx64.efi", [])
    esxi_efi_boot_http: str | None = None
    if efi_boot_pool:
        efi_chosen = _esxi_pick_preferred_path(efi_boot_pool, dest)
        if efi_chosen.is_file():
            mboot_dest = efi_chosen.parent / "mboot.efi"
            shutil.copy2(efi_chosen, mboot_dest)
            _esxi_ensure_lowercase_http_mirrors(dest, [mboot_dest])
            esxi_efi_boot_http = (
                f"{base}/{_esxi_lowercase_posix_rel(_esxi_rel_from_dest(dest, mboot_dest))}"
            )
        else:
            logger.warning("ESXi : bootx64.efi illisible — entrée menu UEFI absente.")
    else:
        logger.warning("ESXi : bootx64.efi absent — entrée menu UEFI absente.")

    logger.info("ESXi OK — mboot.c32=%s modules=%d", mboot_rel, len(preload))

    out_paths: dict = {
        "kernel_path": f"{base}/{mboot_rel}",
        "esxi_boot_cfg_path": f"{base}/ipxe-boot.cfg",
        "esxi_modules": modules_json,
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
    Après extraction complète d'une ISO EL/Fedora (Rocky, AlmaLinux, CentOS, Fedora, …) dans ``dest``,
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
        logger.warning("%s : initrd non trouvé (cherché %s)", label, ", ".join(initrd_names))

    if not result.get("kernel_path") and not result.get("initrd_path"):
        raise ExtractionError(
            f"Aucun fichier de boot {os_slug} (vmlinuz / initrd) trouvé dans l'ISO."
        )
    if not result.get("kernel_path"):
        raise ExtractionError(
            f"{os_slug} : noyau (vmlinuz) introuvable après extraction complète de l’ISO."
        )
    if not result.get("initrd_path"):
        raise ExtractionError(
            f"{os_slug} : initrd introuvable (essayé : {', '.join(initrd_names)}). "
            "Vérifiez que l’ISO contient bien images/pxeboot/ ou isolinux/ ; en dernier recours utilisez l’ISO « Everything » ou netinst."
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
