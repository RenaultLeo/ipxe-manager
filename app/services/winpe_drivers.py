"""
Pilotes WinPE : ``/srv/ipxe/http/boot/drivers/`` + ``drivers.json`` (catalogue par type de machine).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

DRIVERS_DIRNAME = "drivers"
CATALOG_FILENAME = "drivers.json"
STAGING_DIRNAME = "_staging"
MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
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


def staging_root() -> Path:
    path = drivers_root() / STAGING_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_zip_part_path(upload_id: int) -> Path:
    return staging_root() / f"{upload_id}.zip.part"


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


def upsert_catalog_label(label: str, slug: str) -> dict[str, Any]:
    """Force la clé catalogue (libellé wizard) après upload ZIP."""
    folder = folder_for_slug(slug)
    cat = load_catalog()
    for key in list(cat.keys()):
        meta = cat.get(key)
        if isinstance(meta, dict) and meta.get("slug") == slug and key != label:
            del cat[key]
    entry = {
        "path": rel_folder_path(slug),
        "count": count_driver_files(folder),
        "slug": slug,
    }
    cat[label] = entry
    catalog_path().write_text(
        json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return entry


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


def _zip_member_is_junk(name: str) -> bool:
    norm = (name or "").replace("\\", "/").lstrip("/")
    if not norm or norm.endswith("/"):
        return True
    parts = norm.split("/")
    if any(p in (".", "..") or p.startswith(".") for p in parts):
        return True
    if norm.startswith("__MACOSX/"):
        return True
    return False


def safe_extract_zip_archive(zip_path: Path, dest_folder: Path) -> tuple[int, int]:
    """
    Dézippe dans ``dest_folder`` (fusion, sans effacer l'existant).
    Retourne (fichiers extraits, nombre de .inf).
    """
    if not zip_path.is_file():
        raise FileNotFoundError(f"Archive absente : {zip_path}")

    dest_folder.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_folder.resolve()

    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Fichier ZIP invalide ou archive corrompue.")

    extracted = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or _zip_member_is_junk(info.filename):
                continue
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ValueError(
                    f"Fichier trop volumineux dans le ZIP : {Path(info.filename).name}"
                )
            rel = info.filename.replace("\\", "/").lstrip("/")
            target = (dest_resolved / rel).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise ValueError(f"Chemin non autorise dans le ZIP : {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted += 1

    inf_count = count_driver_files(dest_folder)
    if inf_count == 0:
        raise ValueError(
            "Aucun fichier .inf dans le ZIP — verifiez que l archive contient des pilotes Windows."
        )
    return extracted, inf_count


def process_driver_zip_upload(
    *,
    zip_path: Path,
    label: str,
    slug: str,
) -> dict[str, Any]:
    """Extrait le ZIP dans ``boot/drivers/<slug>/`` et regenere ``drivers.json``."""
    folder = folder_for_slug(slug)
    extracted, inf_count = safe_extract_zip_archive(zip_path, folder)
    rebuild_catalog()
    meta = upsert_catalog_label(label, slug)
    return {
        "label": label,
        "slug": slug,
        "path": meta.get("path") or rel_folder_path(slug),
        "count": int(meta.get("count") or inf_count),
        "extracted_files": extracted,
        "inf_count": inf_count,
    }


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
