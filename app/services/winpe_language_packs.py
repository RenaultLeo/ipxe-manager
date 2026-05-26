"""Language packs WinPE : ``boot/language-packs/<locale>/`` + ``language-packs.json``."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.winpe_locale_presets import ui_language_by_id

logger = logging.getLogger(__name__)

PACKS_DIRNAME = "language-packs"
CATALOG_FILENAME = "language-packs.json"
_LOCALE_ID_RE = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_IGNORE_REGION = frozenset(
    {
        "amd64",
        "x86",
        "arm64",
        "wow64",
        "wow",
        "neutral",
        "microsoft",
        "windows",
        "package",
        "cab",
    }
)
# Ordre : motifs les plus fiables en premier (noms .cab Windows Languages).
_CAB_LOCALE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"Client-Language-Pack[_-](?P<lang>[a-z]{2,3})[_-](?P<reg>[a-z0-9]{2,8})",
        re.I,
    ),
    re.compile(
        r"Language[_-]?Pack[_-](?P<lang>[a-z]{2,3})[_-](?P<reg>[a-z0-9]{2,8})",
        re.I,
    ),
    re.compile(
        r"LanguageFeatures[_-][A-Za-z]+[_-](?P<lang>[a-z]{2,3})[_-](?P<reg>[a-z0-9]{2,8})",
        re.I,
    ),
    re.compile(r"~(?P<lang>[a-z]{2,3})-(?P<reg>[a-zA-Z0-9]{2,8})~", re.I),
    re.compile(r"[_-](?P<lang>[a-z]{2,3})-(?P<reg>[a-zA-Z0-9]{2,8})[_-]", re.I),
    re.compile(r"[_-](?P<lang>[a-z]{2,3})[_-](?P<reg>[a-z0-9]{2,8})[_-]", re.I),
)


def packs_root() -> Path:
    root = settings.boot_dir / PACKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def catalog_path() -> Path:
    return packs_root() / CATALOG_FILENAME


def normalize_locale_parts(lang: str, region: str) -> str:
    """BCP-47 Windows (ex. fr-fr → fr-FR, en-us → en-US)."""
    language = (lang or "").strip().lower()[:3]
    reg = (region or "").strip()
    if not language or not reg:
        raise ValueError("locale incomplete")
    reg_lower = reg.lower()
    if reg_lower in _IGNORE_REGION:
        raise ValueError("region ignored")
    if len(reg) == 2 and reg.isalpha():
        region_norm = reg.upper()
    else:
        region_norm = reg[0].upper() + reg[1:].lower() if reg else reg
    candidate = f"{language}-{region_norm}"
    if not _LOCALE_ID_RE.match(candidate):
        raise ValueError("locale invalid")
    return candidate


def canonical_locale_id(locale_id: str) -> str:
    """Aligne sur les presets UI si connus, sinon normalise la casse."""
    raw = (locale_id or "").strip()
    if not raw:
        raise ValueError("locale empty")
    preset = ui_language_by_id(raw)
    if preset:
        return preset["id"]
    from app.services.winpe_locale_presets import UI_LANGUAGES

    raw_lower = raw.lower()
    for row in UI_LANGUAGES:
        if row["id"].lower() == raw_lower:
            return row["id"]
    if "-" in raw:
        lang, reg = raw.split("-", 1)
        return normalize_locale_parts(lang, reg)
    return raw


def locale_id_from_cab_filename(filename: str) -> str | None:
    """Extrait fr-FR, en-US, etc. depuis un nom de .cab Language Pack."""
    name = Path(filename or "").name
    if not name.lower().endswith(".cab"):
        return None
    stem = name[:-4]
    for pattern in _CAB_LOCALE_PATTERNS:
        for match in pattern.finditer(stem):
            lang = match.group("lang")
            reg = match.group("reg")
            if not lang or not reg:
                continue
            if reg.lower() in _IGNORE_REGION:
                continue
            try:
                return canonical_locale_id(normalize_locale_parts(lang, reg))
            except ValueError:
                continue
    return None


def locale_id_to_slug(locale_id: str) -> str:
    loc = (locale_id or "").strip()
    if not _LOCALE_ID_RE.match(loc):
        raise ValueError(
            "Identifiant de langue invalide (ex. fr-FR, en-US, de-DE)."
        )
    slug = loc.lower()
    if not _SLUG_RE.match(slug):
        raise ValueError("Identifiant de langue invalide.")
    return slug


def rel_folder_path(folder_slug: str) -> str:
    return f"{PACKS_DIRNAME}/{folder_slug}"


def folder_for_locale_id(locale_id: str) -> Path:
    slug = locale_id_to_slug(locale_id)
    path = packs_root() / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def count_cab_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.glob("*.cab") if f.is_file())


def list_cab_files(folder: Path) -> list[Path]:
    """Ordre DISM : pack principal Language-Pack d'abord, puis le reste."""
    if not folder.is_dir():
        return []

    def sort_key(p: Path) -> tuple[int, str]:
        name = p.name.lower()
        if "language-pack" in name or "client-language" in name:
            return (0, name)
        if "language-features" in name or "language-experience" in name:
            return (1, name)
        return (2, name)

    return sorted(
        (f for f in folder.glob("*.cab") if f.is_file()),
        key=sort_key,
    )


def _label_for_locale(locale_id: str) -> str:
    preset = ui_language_by_id(locale_id)
    if preset:
        return preset["label"]
    return locale_id


def rebuild_catalog() -> dict[str, dict[str, Any]]:
    root = packs_root()
    catalog: dict[str, dict[str, Any]] = {}
    existing: dict[str, dict[str, Any]] = {}
    cp = catalog_path()
    if cp.is_file():
        try:
            raw = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, dict) and v.get("id"):
                        existing[str(v["id"])] = v
                    elif isinstance(v, dict):
                        existing[k] = {**v, "id": k}
        except (json.JSONDecodeError, OSError):
            logger.warning("language-packs.json illisible, reconstruction depuis le disque")

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        slug = entry.name
        locale_id = None
        for lid, meta in existing.items():
            if isinstance(meta, dict) and (meta.get("slug") or "").lower() == slug:
                locale_id = lid
                break
        if not locale_id:
            parts = slug.split("-", 1)
            if len(parts) == 2:
                try:
                    locale_id = canonical_locale_id(normalize_locale_parts(parts[0], parts[1]))
                except ValueError:
                    locale_id = slug
            else:
                locale_id = slug
        cab_count = count_cab_files(entry)
        if cab_count == 0:
            continue
        catalog[locale_id] = {
            "id": locale_id,
            "label": _label_for_locale(locale_id),
            "path": rel_folder_path(slug),
            "slug": slug,
            "cab_count": cab_count,
        }

    for locale_id, meta in existing.items():
        if not isinstance(meta, dict):
            continue
        if locale_id in catalog:
            continue
        slug = (meta.get("slug") or locale_id_to_slug(locale_id)).lower()
        folder = root / slug
        cab_count = count_cab_files(folder)
        if cab_count > 0:
            catalog[locale_id] = {
                "id": locale_id,
                "label": str(meta.get("label") or _label_for_locale(locale_id)),
                "path": rel_folder_path(slug),
                "slug": slug,
                "cab_count": cab_count,
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
        if isinstance(raw, dict):
            out: dict[str, dict[str, Any]] = {}
            for k, v in raw.items():
                if not isinstance(v, dict):
                    continue
                locale_id = str(v.get("id") or k)
                slug = (v.get("slug") or locale_id_to_slug(locale_id)).lower()
                folder = packs_root() / slug
                out[locale_id] = {
                    "id": locale_id,
                    "label": str(v.get("label") or _label_for_locale(locale_id)),
                    "path": rel_folder_path(slug),
                    "slug": slug,
                    "cab_count": count_cab_files(folder),
                }
            return out
    except (json.JSONDecodeError, OSError):
        pass
    return rebuild_catalog()


def catalog_for_template() -> list[dict[str, Any]]:
    cat = load_catalog()
    rows = [
        {
            "id": meta["id"],
            "label": meta["label"],
            "path": meta["path"],
            "slug": meta["slug"],
            "cab_count": int(meta["cab_count"] or 0),
        }
        for meta in sorted(cat.values(), key=lambda m: m["label"].casefold())
    ]
    return rows


def catalog_locale_ids() -> set[str]:
    return set(load_catalog().keys())


def ui_languages_for_deploy_embed() -> list[dict[str, str]]:
    """Langues interface proposées au wizard = packs présents sur le serveur."""
    from app.services.winpe_locale_presets import _ui_row

    out: list[dict[str, str]] = []
    for locale_id, meta in sorted(load_catalog().items(), key=lambda x: x[1]["label"].casefold()):
        preset = ui_language_by_id(locale_id)
        if preset:
            out.append(dict(preset))
        else:
            out.append(_ui_row(locale_id, meta["label"]))
    return out


def default_deploy_ui_language_id() -> str:
    cat = load_catalog()
    if "fr-FR" in cat:
        return "fr-FR"
    if cat:
        return sorted(cat.keys(), key=str.casefold)[0]
    return "fr-FR"


def resolve_locale_upload(
    *,
    locale_kind: str,
    locale_id: str = "",
    new_locale_id: str = "",
) -> tuple[str, str, Path]:
    kind = (locale_kind or "").strip().lower()
    if kind == "existing":
        lid = (locale_id or "").strip()
        if not lid:
            raise ValueError("Choisissez une langue.")
        cat = load_catalog()
        if lid not in cat:
            folder = folder_for_locale_id(lid)
            if count_cab_files(folder) == 0:
                raise ValueError("Cette langue n'a pas encore de fichiers .cab sur le serveur.")
        else:
            meta = cat[lid]
            folder = packs_root() / meta["slug"]
        return lid, locale_id_to_slug(lid), folder

    if kind == "new":
        lid = (new_locale_id or "").strip()
        if not lid:
            raise ValueError("Indiquez l'identifiant de langue (ex. en-US).")
        slug = locale_id_to_slug(lid)
        folder = folder_for_locale_id(lid)
        return lid, slug, folder

    raise ValueError("Mode de sélection de langue invalide.")


async def _write_one_cab(
    dest: Path,
    uf,
    *,
    chunk_size: int = 1024 * 1024,
) -> bool:
    size = 0
    with open(dest, "wb") as out:
        while chunk := await uf.read(chunk_size):
            out.write(chunk)
            size += len(chunk)
    return size > 0


async def save_uploaded_cab_files(
    folder: Path,
    files: list,
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, list[str]]:
    saved: list[str] = []
    for uf in files:
        fname = Path(getattr(uf, "filename", None) or "file").name
        if not fname.lower().endswith(".cab"):
            continue
        if ".." in fname or "/" in fname or "\\" in fname:
            raise ValueError("Nom de fichier invalide.")
        dest = folder / fname
        if await _write_one_cab(dest, uf, chunk_size=chunk_size):
            saved.append(fname)
    return len(saved), saved


async def save_uploaded_cab_files_auto(
    files: list,
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, dict[str, list[str]], list[str]]:
    """
    Enregistre les .cab dans boot/language-packs/<locale>/ selon le nom de fichier.
    Retourne (nombre total, {locale_id: [fichiers]}, fichiers non reconnus).
    """
    buckets: dict[str, list[tuple[str, object]]] = {}
    skipped: list[str] = []

    for uf in files:
        fname = Path(getattr(uf, "filename", None) or "file").name
        if not fname.lower().endswith(".cab"):
            continue
        if ".." in fname or "/" in fname or "\\" in fname:
            raise ValueError(f"Nom de fichier invalide : {fname}")
        locale_id = locale_id_from_cab_filename(fname)
        if not locale_id:
            skipped.append(fname)
            continue
        buckets.setdefault(locale_id, []).append((fname, uf))

    if not buckets:
        if skipped:
            raise ValueError(
                "Aucune locale detectee dans les noms de fichier. "
                "Attendu : fr-fr / fr-FR dans le nom (ex. "
                "Microsoft-Windows-Client-Language-Pack_fr-fr_amd64.cab)."
            )
        raise ValueError("Choisissez au moins un fichier .cab.")

    saved_by_locale: dict[str, list[str]] = {}
    total = 0
    for locale_id, items in buckets.items():
        folder = folder_for_locale_id(locale_id)
        names: list[str] = []
        for fname, uf in items:
            dest = folder / fname
            if await _write_one_cab(dest, uf, chunk_size=chunk_size):
                names.append(fname)
                total += 1
        if names:
            saved_by_locale[locale_id] = names

    return total, saved_by_locale, skipped
