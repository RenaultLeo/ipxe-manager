"""
Extracteur ISO simplifié.
Extrait l'ISO complète avec 7z puis cherche les fichiers de boot par nom.
"""
import subprocess
import shutil
import tempfile
from pathlib import Path

from app.config import settings


class ExtractionError(Exception):
    pass


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

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [seven_z, "x", str(iso), f"-o{tmp}", "-y"],
            capture_output=True, text=True,
            timeout=settings.extract_timeout,
        )
        if result.returncode not in (0, 1):
            raise ExtractionError(f"7z a échoué (code {result.returncode}) :\n{result.stderr}")

        extracted = Path(tmp)

        if os_slug in ("windows", "winpe"):
            return _find_windows(extracted, dest, os_slug, version_slug)
        else:
            return _find_linux(extracted, dest, os_slug, version_slug)


# ── Linux ─────────────────────────────────────────────────────────────────────

KERNEL_NAMES  = {"vmlinuz", "vmlinux", "linux", "kernel"}
INITRD_NAMES  = {"initrd", "initrd.gz", "initrd.lz4", "initrd.xz",
                  "initrd.img", "initramfs.img", "initramfs"}

def _find_linux(src: Path, dest: Path, os_slug: str, version_slug: str) -> dict:
    result = {}
    base = f"boot/{os_slug}/{version_slug}"

    kernel = _find_file(src, KERNEL_NAMES)
    if kernel:
        shutil.copy2(kernel, dest / "vmlinuz")
        result["kernel_path"] = f"{base}/vmlinuz"

    initrd = _find_file(src, INITRD_NAMES)
    if initrd:
        suffix = initrd.suffix or ""
        fname = f"initrd{suffix}"
        shutil.copy2(initrd, dest / fname)
        result["initrd_path"] = f"{base}/{fname}"

    if not result:
        raise ExtractionError(
            "Aucun fichier vmlinuz/initrd trouvé dans l'ISO. "
            "Uploader les fichiers manuellement via Fichiers Boot."
        )
    return result


# ── Windows ───────────────────────────────────────────────────────────────────

def _find_windows(src: Path, dest: Path, os_slug: str, version_slug: str) -> dict:
    result = {}
    base = f"boot/{os_slug}/{version_slug}"

    # BCD
    bcd = _find_file(src, {"bcd"})
    if bcd:
        shutil.copy2(bcd, dest / "BCD")
        result["bcd_path"] = f"{base}/BCD"

    # boot.sdi
    sdi = _find_file(src, {"boot.sdi"})
    if sdi:
        shutil.copy2(sdi, dest / "boot.sdi")
        result["boot_sdi_path"] = f"{base}/boot.sdi"

    # boot.wim (sources/boot.wim — le plus gros)
    wim = _find_file(src, {"boot.wim"})
    if wim:
        shutil.copy2(wim, dest / "boot.wim")
        result["boot_wim_path"] = f"{base}/boot.wim"

    # bootmgr.efi (UEFI)
    efi = _find_file(src, {"bootmgr.efi"})
    if efi:
        shutil.copy2(efi, dest / "bootmgr.efi")
        result["bootmgr_path"] = f"{base}/bootmgr.efi"

    if not result:
        raise ExtractionError(
            "Aucun fichier Windows (BCD/boot.sdi/boot.wim) trouvé dans l'ISO. "
            "Uploader les fichiers manuellement via Fichiers Boot."
        )
    return result


# ── Utilitaire ────────────────────────────────────────────────────────────────

def _find_file(root: Path, names: set[str]) -> Path | None:
    """
    Cherche récursivement un fichier dont le nom (lowercase) est dans `names`.
    Retourne le plus grand fichier trouvé (évite les petits stubs).
    """
    candidates = [
        f for f in root.rglob("*")
        if f.is_file() and f.name.lower() in names
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def cleanup_boot_files(os_slug: str, version_id: int):
    dest = settings.boot_dir / os_slug / str(version_id)
    if dest.exists():
        shutil.rmtree(dest)
