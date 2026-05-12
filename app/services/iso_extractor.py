"""
Extracteur ISO par distribution.
Utilise 7z pour extraire l'ISO, puis cherche les fichiers de boot
avec des règles spécifiques à chaque distro.
"""
import logging
import subprocess
import shutil
import tempfile
from pathlib import Path

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
    # Ubuntu Server — vmlinuz + initrd (ou initrd.lz / initrd.gz)
    "ubuntu": {
        "type":    "linux",
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
    # VMware ESXi — pas de boot standard via kernel/initrd (skip extraction)
    "esxi": {
        "type":    "skip",
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

    if rule["type"] == "windows":
        # Windows : extraction COMPLÈTE de l'ISO directement dans dest
        # (tous les fichiers de l'ISO sont nécessaires pour setup.exe via Samba)
        logger.info("Extraction complète Windows → %s", dest)
        proc = subprocess.run(
            [seven_z, "x", str(iso), f"-o{str(dest)}", "-y"],
            capture_output=True, text=True,
            timeout=settings.extract_timeout,
        )
        if proc.returncode not in (0, 1):
            raise ExtractionError(
                f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
            )
        # Corriger les permissions pour que Nginx (www-data) puisse servir les fichiers
        _fix_permissions(dest)
        paths = _find_windows_in_dest(dest, os_slug, version_slug)
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
        out_name = "vmlinuz"
        shutil.copy2(kernel, dest / out_name)
        result["kernel_path"] = f"{base}/{out_name}"
        logger.info("Kernel : %s → %s", kernel.name, out_name)
    else:
        logger.warning("Kernel non trouvé pour %s", os_slug)

    # ── Initrd ──
    initrd = _find_by_priority(src, initrd_names, mode="initrd")
    if initrd:
        suffix = initrd.suffix or ""
        fname = f"initrd{suffix}"
        shutil.copy2(initrd, dest / fname)
        result["initrd_path"] = f"{base}/{fname}"
        logger.info("Initrd : %s → %s", initrd.name, fname)
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
