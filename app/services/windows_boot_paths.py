"""
Chemins Windows / WinPE : conserver la casse du disque (ex. SOURCES/BOOT.WIM).

Le dépôt HTTP sous Linux est sensible à la casse ; la BDD et les menus iPXE
doivent utiliser les mêmes segments que les fichiers extraits.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings

# Layout ADK / ISO WinPE le plus courant sur le disque
DEFAULT_BOOT_WIM_REL_SUFFIX = "SOURCES/BOOT.WIM"


def _http_root_path() -> Path:
    return Path(settings.http_root).expanduser().resolve()


def http_rel_path(file_path: Path) -> str:
    root = _http_root_path()
    return file_path.resolve().relative_to(root).as_posix()


def resolve_http_rel(rel: str) -> Path:
    """Résout un chemin relatif HTTP vers le fichier réel (recherche insensible à la casse)."""
    clean = rel.replace("\\", "/").strip("/")
    if not clean:
        raise FileNotFoundError(rel)
    root = _http_root_path()
    cur = root
    for part in clean.split("/"):
        if not cur.is_dir():
            raise FileNotFoundError(rel)
        match: Path | None = None
        for child in cur.iterdir():
            if child.name.lower() == part.lower():
                match = child
                break
        if match is None:
            raise FileNotFoundError(rel)
        cur = match
    if not cur.is_file():
        raise FileNotFoundError(rel)
    return cur


def version_dir(os_slug: str, version_slug: str) -> Path:
    return settings.boot_dir / os_slug / version_slug  # boot_dir = Path(http_root)/boot


def rel_under_version(file_path: Path, os_slug: str, version_slug: str) -> str:
    ver = version_dir(os_slug, version_slug)
    rel = file_path.resolve().relative_to(ver.resolve())
    return f"boot/{os_slug}/{version_slug}/{rel.as_posix()}"


def _rank_windows_path(p: Path) -> tuple[int, int, str]:
    parts = {x.lower() for x in p.parts}
    pref = 0
    if "sources" in parts:
        pref -= 4
    if "boot" in parts:
        pref -= 2
    if "efi" in parts and "microsoft" in parts:
        pref -= 1
    return (pref, len(p.parts), str(p))


def find_file_under_version(
    ver_dir: Path,
    *,
    basename: str | None = None,
    bcd: bool = False,
    bootmgr: bool = False,
) -> Path | None:
    if not ver_dir.is_dir():
        return None
    candidates: list[Path] = []
    for p in ver_dir.rglob("*"):
        if not p.is_file():
            continue
        if bcd:
            if p.name.upper() == "BCD" and not p.suffix:
                candidates.append(p)
            continue
        if bootmgr:
            if p.name.lower() in ("bootmgr.efi", "bootmgr"):
                candidates.append(p)
            continue
        if basename and p.name.lower() == basename.lower():
            candidates.append(p)
    if not candidates:
        return None
    return min(candidates, key=_rank_windows_path)


def boot_wim_path_on_disk(ver_dir: Path, stored_rel: str | None = None) -> Path:
    if stored_rel:
        try:
            return resolve_http_rel(stored_rel)
        except FileNotFoundError:
            pass
    found = find_file_under_version(ver_dir, basename="boot.wim")
    if found:
        return found
    default = ver_dir / "SOURCES" / "BOOT.WIM"
    return default


def canonicalize_rel(stored_rel: str | None) -> str:
    """Retourne le chemin relatif avec la casse du fichier sur le disque."""
    if not stored_rel or not str(stored_rel).strip():
        return ""
    try:
        return http_rel_path(resolve_http_rel(stored_rel))
    except FileNotFoundError:
        return stored_rel.replace("\\", "/").lstrip("/")


def version_slug_for_disk(be, version_label: str, version_id: int) -> str:
    """Dossier sous boot/<os>/ — aligné sur boot_wim_path si présent."""
    from app.services.slugify import slugify

    if be and getattr(be, "boot_wim_path", None):
        parts = str(be.boot_wim_path).replace("\\", "/").strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "boot":
            return parts[2]
    return slugify(version_label) if version_label else str(version_id)


def sync_windows_boot_entry_from_disk(
    be,
    os_slug: str,
    version_slug: str,
) -> bool:
    """Met à jour bcd/boot.sdi/boot.wim/bootmgr en BDD selon l'arborescence réelle."""
    ver_dir = version_dir(os_slug, version_slug)
    if not ver_dir.is_dir():
        return False
    changed = False
    pairs: list[tuple[str, Path | None]] = [
        ("boot_wim_path", find_file_under_version(ver_dir, basename="boot.wim")),
        ("bcd_path", find_file_under_version(ver_dir, bcd=True)),
        ("boot_sdi_path", find_file_under_version(ver_dir, basename="boot.sdi")),
        ("bootmgr_path", find_file_under_version(ver_dir, bootmgr=True)),
    ]
    for field, found in pairs:
        if not found:
            continue
        rel = rel_under_version(found, os_slug, version_slug)
        if getattr(be, field, None) != rel:
            setattr(be, field, rel)
            changed = True
    return changed
