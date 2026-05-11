"""
Extracteur ISO.
Extrait l'ISO complète avec 7z puis cherche les fichiers de boot par nom.
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


# ── Noms exacts ou préfixes Linux ─────────────────────────────────────────────

# Préfixes pour matchs partiels (vmlinuz-6.1.0-amd64, initrd.img-5.15…)
KERNEL_PREFIXES = ("vmlinuz", "vmlinux", "linux", "kernel")
INITRD_PREFIXES = ("initrd", "initramfs")

# Extensions acceptées pour initrd
INITRD_EXTENSIONS = {"", ".gz", ".lz", ".lz4", ".xz", ".zst", ".img", ".cpio"}


def _is_kernel(name: str) -> bool:
    n = name.lower()
    return any(n == p or n.startswith(p + "-") or n.startswith(p + ".") for p in KERNEL_PREFIXES)


def _is_initrd(name: str) -> bool:
    n = name.lower()
    for prefix in INITRD_PREFIXES:
        if n == prefix:
            return True
        # initrd.gz, initrd.img, initrd-5.15.img, initramfs-linux.img …
        if n.startswith(prefix):
            stem = n[len(prefix):]
            if not stem or stem[0] in ("-", ".", "_"):
                return True
    return False


# ── Noms exacts Windows ────────────────────────────────────────────────────────

# BCD : fichier sans extension nommé "BCD" ou "bcd"
# boot.sdi : exactement "boot.sdi"
# boot.wim : exactement "boot.wim"
# bootmgr : bootmgr.efi ou bootmgfw.efi
BOOTMGR_NAMES = {"bootmgr.efi", "bootmgfw.efi"}


# ── Point d'entrée ─────────────────────────────────────────────────────────────

def extract_iso(iso_path: str, os_slug: str, version_id: int, version_label: str = "") -> dict:
    from app.services.slugify import slugify
    version_slug = slugify(version_label) if version_label else str(version_id)

    iso = Path(iso_path)
    if not iso.exists():
        raise ExtractionError(f"ISO introuvable : {iso_path}")

    seven_z = shutil.which("7z") or shutil.which("7za")
    if not seven_z:
        raise ExtractionError("7z non installé. Lancer : apt-get install -y p7zip-full")

    dest = settings.boot_dir / os_slug / version_slug
    dest.mkdir(parents=True, exist_ok=True)

    logger.info("Extraction ISO : %s → %s", iso, dest)

    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [seven_z, "x", str(iso), f"-o{tmp}", "-y"],
            capture_output=True, text=True,
            timeout=settings.extract_timeout,
        )
        if proc.returncode not in (0, 1):   # 1 = warnings non bloquants
            raise ExtractionError(
                f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
            )

        extracted = Path(tmp)

        if os_slug in ("windows", "winpe"):
            paths = _find_windows(extracted, dest, os_slug, version_slug)
        else:
            paths = _find_linux(extracted, dest, os_slug, version_slug)

    logger.info("Extraction terminée : %s", paths)
    return paths


# ── Linux ─────────────────────────────────────────────────────────────────────

def _find_linux(src: Path, dest: Path, os_slug: str, version_slug: str) -> dict:
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"

    # ── Kernel ──────────────────────────────────────────────────────────────
    kernel_candidates = [
        f for f in src.rglob("*")
        if f.is_file() and _is_kernel(f.name)
    ]
    logger.debug("Kernel candidats : %s", [str(f) for f in kernel_candidates])

    if kernel_candidates:
        # Préférer le plus gros (évite les stubs EFI de 1 Ko)
        kernel = max(kernel_candidates, key=lambda f: f.stat().st_size)
        shutil.copy2(kernel, dest / "vmlinuz")
        result["kernel_path"] = f"{base}/vmlinuz"
        logger.info("Kernel copié : %s", kernel)
    else:
        logger.warning("Aucun kernel trouvé dans l'ISO")

    # ── Initrd ──────────────────────────────────────────────────────────────
    initrd_candidates = [
        f for f in src.rglob("*")
        if f.is_file()
        and _is_initrd(f.name)
        and f.suffix.lower() in INITRD_EXTENSIONS
    ]
    logger.debug("Initrd candidats : %s", [str(f) for f in initrd_candidates])

    if initrd_candidates:
        initrd = max(initrd_candidates, key=lambda f: f.stat().st_size)
        # Conserver l'extension d'origine
        suffix = initrd.suffix or ""
        fname = f"initrd{suffix}"
        shutil.copy2(initrd, dest / fname)
        result["initrd_path"] = f"{base}/{fname}"
        logger.info("Initrd copié : %s → %s", initrd, fname)
    else:
        logger.warning("Aucun initrd trouvé dans l'ISO")

    if not result:
        raise ExtractionError(
            "Aucun fichier vmlinuz/initrd trouvé dans l'ISO. "
            "Uploader les fichiers manuellement via Fichiers Boot."
        )
    return result


# ── Windows ───────────────────────────────────────────────────────────────────

def _find_windows(src: Path, dest: Path, os_slug: str, version_slug: str) -> dict:
    result: dict = {}
    base = f"boot/{os_slug}/{version_slug}"

    # ── BCD ─────────────────────────────────────────────────────────────────
    # Le fichier BCD n'a pas d'extension et se nomme exactement "BCD"
    bcd_candidates = [
        f for f in src.rglob("*")
        if f.is_file() and f.name.upper() == "BCD" and not f.suffix
    ]
    logger.debug("BCD candidats : %s", [str(f) for f in bcd_candidates])
    if bcd_candidates:
        bcd = max(bcd_candidates, key=lambda f: f.stat().st_size)
        shutil.copy2(bcd, dest / "BCD")
        result["bcd_path"] = f"{base}/BCD"
        logger.info("BCD copié : %s", bcd)
    else:
        logger.warning("BCD non trouvé dans l'ISO")

    # ── boot.sdi ────────────────────────────────────────────────────────────
    sdi_candidates = [
        f for f in src.rglob("*")
        if f.is_file() and f.name.lower() == "boot.sdi"
    ]
    logger.debug("boot.sdi candidats : %s", [str(f) for f in sdi_candidates])
    if sdi_candidates:
        sdi = max(sdi_candidates, key=lambda f: f.stat().st_size)
        shutil.copy2(sdi, dest / "boot.sdi")
        result["boot_sdi_path"] = f"{base}/boot.sdi"
        logger.info("boot.sdi copié : %s", sdi)
    else:
        logger.warning("boot.sdi non trouvé dans l'ISO")

    # ── boot.wim ────────────────────────────────────────────────────────────
    wim_candidates = [
        f for f in src.rglob("*")
        if f.is_file() and f.name.lower() == "boot.wim"
    ]
    logger.debug("boot.wim candidats : %s", [str(f) for f in wim_candidates])
    if wim_candidates:
        # Prendre le plus gros (sources/boot.wim plutôt qu'un éventuel stub)
        wim = max(wim_candidates, key=lambda f: f.stat().st_size)
        shutil.copy2(wim, dest / "boot.wim")
        result["boot_wim_path"] = f"{base}/boot.wim"
        logger.info("boot.wim copié : %s (%.1f Mo)", wim, wim.stat().st_size / 1_048_576)
    else:
        logger.warning("boot.wim non trouvé dans l'ISO")

    # ── bootmgr.efi / bootmgfw.efi ──────────────────────────────────────────
    efi_candidates = [
        f for f in src.rglob("*")
        if f.is_file() and f.name.lower() in BOOTMGR_NAMES
    ]
    logger.debug("bootmgr.efi candidats : %s", [str(f) for f in efi_candidates])
    if efi_candidates:
        efi = max(efi_candidates, key=lambda f: f.stat().st_size)
        shutil.copy2(efi, dest / "bootmgr.efi")
        result["bootmgr_path"] = f"{base}/bootmgr.efi"
        logger.info("bootmgr.efi copié : %s", efi)
    else:
        logger.warning("bootmgr.efi / bootmgfw.efi non trouvé dans l'ISO")

    if not result:
        raise ExtractionError(
            "Aucun fichier Windows (BCD / boot.sdi / boot.wim) trouvé dans l'ISO. "
            "Uploader les fichiers manuellement via Fichiers Boot."
        )
    return result


# ── Nettoyage ─────────────────────────────────────────────────────────────────

def cleanup_boot_files(os_slug: str, version_label: str, version_id: int = 0):
    from app.services.slugify import slugify
    version_slug = slugify(version_label) if version_label else str(version_id)
    dest = settings.boot_dir / os_slug / version_slug
    if dest.exists():
        shutil.rmtree(dest)
        logger.info("Dossier supprimé : %s", dest)
