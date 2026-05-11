"""
Extracts boot files from an ISO image.
Supports Linux ISOs (vmlinuz/initrd) and Windows ISOs (boot.wim via wimlib).
"""
import subprocess
import shutil
import tempfile
from pathlib import Path

from app.config import settings


class ExtractionError(Exception):
    pass


def _run(cmd: list[str], timeout: int = settings.extract_timeout):
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise ExtractionError(result.stderr or result.stdout)
    return result.stdout


def extract_iso(iso_path: str, os_slug: str, version_id: int) -> dict:
    """
    Mount the ISO (loop) or use 7z, then copy key boot files to
    http_root/boot/<os_slug>/<version_id>/.
    Returns dict with discovered file paths (relative to http_root).
    """
    iso = Path(iso_path)
    if not iso.exists():
        raise ExtractionError(f"ISO introuvable : {iso_path}")

    dest = settings.boot_dir / os_slug / str(version_id)
    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        _mount_iso(iso, tmpdir)
        result = _copy_boot_files(Path(tmpdir), dest, os_slug)

    return result


def _mount_iso(iso: Path, mount_point: str):
    """Try 7z extraction first (no root needed); fall back to mount -o loop."""
    seven_z = shutil.which("7z")
    if seven_z:
        _run([seven_z, "x", str(iso), f"-o{mount_point}", "-y"])
    else:
        # Requires root
        _run(["mount", "-o", "loop,ro", str(iso), mount_point])


def _copy_boot_files(src: Path, dest: Path, os_slug: str) -> dict:
    result: dict = {}

    if os_slug == "windows":
        result.update(_extract_windows(src, dest))
    else:
        result.update(_extract_linux(src, dest))

    return result


def _extract_linux(src: Path, dest: Path) -> dict:
    result: dict = {}
    candidates_kernel = [
        "casper/vmlinuz", "live/vmlinuz", "isolinux/vmlinuz",
        "boot/vmlinuz", "vmlinuz",
    ]
    candidates_initrd = [
        "casper/initrd", "casper/initrd.gz", "casper/initrd.lz4",
        "live/initrd.img", "isolinux/initrd.img",
        "boot/initrd.img", "initrd.img",
    ]

    for rel in candidates_kernel:
        f = src / rel
        if f.exists():
            shutil.copy2(f, dest / "vmlinuz")
            result["kernel_path"] = f"boot/{dest.parent.name}/{dest.name}/vmlinuz"
            break

    for rel in candidates_initrd:
        f = src / rel
        if f.exists():
            suffix = f.suffix or ""
            fname = f"initrd{suffix}"
            shutil.copy2(f, dest / fname)
            result["initrd_path"] = f"boot/{dest.parent.name}/{dest.name}/{fname}"
            break

    return result


def _extract_windows(src: Path, dest: Path) -> dict:
    result: dict = {}

    # Try wimlib-imagex first
    wim_src = src / "sources" / "boot.wim"
    if wim_src.exists():
        shutil.copy2(wim_src, dest / "boot.wim")
        result["boot_wim_path"] = f"boot/{dest.parent.name}/{dest.name}/boot.wim"

    # Copy BCD and bootmgr for UEFI HTTP boot
    for name in ["bootmgr", "bootmgr.efi", "boot/BCD", "EFI/Microsoft/Boot/BCD"]:
        f = src / name
        if f.exists():
            out = dest / Path(name).name
            shutil.copy2(f, out)

    return result


def cleanup_boot_files(os_slug: str, version_id: int):
    dest = settings.boot_dir / os_slug / str(version_id)
    if dest.exists():
        shutil.rmtree(dest)
