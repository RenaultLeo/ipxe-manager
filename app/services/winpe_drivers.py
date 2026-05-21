"""
Pilotes WinPE : ``/srv/ipxe/http/boot/drivers/`` + ``drivers.json`` (catalogue par type de machine).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

DRIVERS_DIRNAME = "drivers"
CATALOG_FILENAME = "drivers.json"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_DRIVER_EXTENSIONS = {
    ".inf",
    ".sys",
    ".cat",
    ".dll",
    ".exe",
    ".cab",
}


def drivers_root() -> Path:
    root = settings.boot_dir / DRIVERS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def catalog_path() -> Path:
    return drivers_root() / CATALOG_FILENAME


def rel_folder_path(folder_slug: str) -> str:
    return f"{DRIVERS_DIRNAME}/{folder_slug}"


def folder_for_slug(folder_slug: str) -> Path:
    if not _SLUG_RE.match(folder_slug):
        raise ValueError("Nom de dossier invalide.")
    path = drivers_root() / folder_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_machine_slug(raw: str) -> str:
    s = slugify(raw or "").strip().lower()
    if not s:
        raise ValueError("Nom du type de machine invalide.")
    if not _SLUG_RE.match(s):
        raise ValueError(
            "Nom du type de machine invalide — lettres minuscules, chiffres, « . », « - », « _ »."
        )
    return s


def display_key_from_name(name: str, slug: str) -> str:
    label = (name or "").strip()
    return label if label else slug


def count_driver_files(folder: Path) -> int:
    """Nombre de fichiers .inf (paquets DISM), pas tous les fichiers."""
    if not folder.is_dir():
        return 0
    n = 0
    for f in folder.rglob("*.inf"):
        if f.is_file() and not f.name.startswith("."):
            n += 1
    return n


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    if not base or base in (".", ".."):
        raise ValueError("Nom de fichier invalide.")
    if ".." in base or "/" in base or "\\" in base:
        raise ValueError("Nom de fichier invalide.")
    return base


def rebuild_catalog() -> dict[str, dict[str, Any]]:
    """Scanne ``drivers/*`` et réécrit ``drivers.json``."""
    root = drivers_root()
    catalog: dict[str, dict[str, Any]] = {}
    existing_json: dict[str, dict[str, Any]] = {}
    cp = catalog_path()
    if cp.is_file():
        try:
            raw = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing_json = raw
        except (json.JSONDecodeError, OSError):
            logger.warning("drivers.json illisible, reconstruction depuis le disque")

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        slug = entry.name
        if slug.startswith("."):
            continue
        prev = existing_json.get(slug) or {}
        if isinstance(prev, dict) and prev.get("label"):
            label = str(prev["label"])
        else:
            for k, v in existing_json.items():
                if isinstance(v, dict) and v.get("path", "").rstrip("/").endswith(f"/{slug}"):
                    label = k
                    break
            else:
                label = slug.replace("-", " ").replace("_", " ").title()
        catalog[label] = {
            "path": rel_folder_path(slug),
            "count": count_driver_files(entry),
            "slug": slug,
        }

    for label, meta in existing_json.items():
        if not isinstance(meta, dict):
            continue
        path_s = (meta.get("path") or "").replace("\\", "/").strip("/")
        if not path_s.startswith(f"{DRIVERS_DIRNAME}/"):
            continue
        slug = path_s.split("/", 1)[-1]
        folder = root / slug
        if folder.is_dir() and label not in catalog:
            catalog[label] = {
                "path": rel_folder_path(slug),
                "count": count_driver_files(folder),
                "slug": slug,
            }

    cp.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog


def load_catalog() -> dict[str, dict[str, Any]]:
    if not catalog_path().is_file():
        return rebuild_catalog()
    try:
        raw = json.loads(catalog_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw:
            for label, meta in list(raw.items()):
                if not isinstance(meta, dict):
                    continue
                path_s = (meta.get("path") or "").replace("\\", "/")
                slug = path_s.rstrip("/").split("/")[-1] if path_s else ""
                if slug:
                    folder = drivers_root() / slug
                    meta["count"] = count_driver_files(folder)
                    meta["slug"] = slug
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return rebuild_catalog()


def catalog_for_template() -> list[dict[str, Any]]:
    """Liste triée pour le template (label, path, count, slug)."""
    cat = load_catalog()
    rows: list[dict[str, Any]] = []
    for label, meta in sorted(cat.items(), key=lambda x: x[0].lower()):
        if not isinstance(meta, dict):
            continue
        rows.append(
            {
                "label": label,
                "path": meta.get("path") or "",
                "count": int(meta.get("count") or 0),
                "slug": meta.get("slug") or "",
            }
        )
    return rows


def resolve_machine_upload(
    *,
    machine_kind: str,
    machine_key: str = "",
    new_machine_name: str = "",
) -> tuple[str, str, Path]:
    """
    Retourne (clé catalogue, slug dossier, chemin dossier).
    machine_kind : ``existing`` | ``new``
    """
    kind = (machine_kind or "").strip().lower()
    if kind == "existing":
        key = (machine_key or "").strip()
        if not key:
            raise ValueError("Choisissez un type de machine.")
        cat = load_catalog()
        meta = cat.get(key)
        if not meta or not isinstance(meta, dict):
            raise ValueError("Type de machine introuvable dans le catalogue.")
        path_s = (meta.get("path") or "").replace("\\", "/").strip("/")
        slug = path_s.split("/")[-1] if path_s else (meta.get("slug") or "")
        if not slug:
            slug = normalize_machine_slug(key)
        folder = folder_for_slug(slug)
        return key, slug, folder

    if kind == "new":
        name = (new_machine_name or "").strip()
        if not name:
            raise ValueError("Indiquez le nom du nouveau type de machine.")
        slug = normalize_machine_slug(name)
        folder = folder_for_slug(slug)
        key = display_key_from_name(name, slug)
        return key, slug, folder

    raise ValueError("Mode de sélection de machine invalide.")


def register_machine_in_catalog(label: str, slug: str) -> None:
    cat = load_catalog()
    cat[label] = {
        "path": rel_folder_path(slug),
        "count": count_driver_files(drivers_root() / slug),
        "slug": slug,
    }
    catalog_path().write_text(
        json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def save_uploaded_driver_files(
    folder: Path,
    files: list,
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, list[str]]:
    """Écrit les fichiers uploadés dans ``folder``. Retourne (nb fichiers, noms)."""
    saved: list[str] = []
    for uf in files:
        fname = _safe_filename(getattr(uf, "filename", None) or "file")
        dest = folder / fname
        size = 0
        with open(dest, "wb") as out:
            while chunk := await uf.read(chunk_size):
                out.write(chunk)
                size += len(chunk)
        if size > 0:
            saved.append(fname)
    return len(saved), saved


def smb_drivers_unc(host: str, share: str, slug: str) -> str:
    return f"\\\\{host}\\{share}\\{DRIVERS_DIRNAME}\\{slug}"
