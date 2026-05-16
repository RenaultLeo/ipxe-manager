"""
Extraction ISO pilotée par ``OsType`` :
- Liste de **noms de fichier** (ex. ``vmlinuz``, ``init``) avec nombre max ;
- anciens entrées ``pattern`` (fnmatch relatif ISO) encore prises en charge.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import settings
from app.models.models import OsType
from app.services.iso_extractor import (
    ExtractionError,
    _GENERIC_RULE,
    _find_in_dest,
    _find_windows_in_dest,
    _fix_permissions,
)

logger = logging.getLogger(__name__)


@dataclass
class _UnifiedSpec:
    pattern: str | None
    basename: str | None
    max_n: int


def uses_custom_extract_plan(ot: OsType) -> bool:
    if getattr(ot, "extract_full_iso", False):
        return True
    try:
        data = json.loads(getattr(ot, "extract_paths_json", None) or "[]")
        return isinstance(data, list) and len(data) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def _normalize_specs(specs_raw: list) -> list[_UnifiedSpec]:
    out: list[_UnifiedSpec] = []
    for obj in specs_raw:
        if not isinstance(obj, dict):
            continue
        try:
            max_n = max(1, int(obj.get("max", 1)))
        except (TypeError, ValueError):
            max_n = 1

        fname = str(obj.get("filename") or obj.get("name") or "").strip()
        if fname:
            bn = PurePosixPath(fname.replace("\\", "/")).name
            if bn:
                out.append(_UnifiedSpec(pattern=None, basename=bn, max_n=max_n))
            continue

        pat = str(obj.get("pattern") or "").strip()
        if not pat:
            continue
        if "*" in pat or "?" in pat or ("[" in pat and "]" in pat):
            out.append(_UnifiedSpec(pattern=pat, basename=None, max_n=max_n))
        else:
            bn = PurePosixPath(pat).name
            if bn:
                out.append(_UnifiedSpec(pattern=None, basename=bn, max_n=max_n))
    return out


def try_extract_with_plan(
    iso_path: str,
    ot: OsType,
    version_id: int,
    version_label: str,
) -> dict | None:
    if not uses_custom_extract_plan(ot):
        return None

    from app.services.slugify import slugify

    os_slug = ot.slug
    version_slug = slugify(version_label) if version_label else str(version_id)
    iso = Path(iso_path)
    if not iso.exists():
        raise ExtractionError(f"ISO introuvable : {iso_path}")

    seven_z = shutil.which("7z") or shutil.which("7za")
    if not seven_z:
        raise ExtractionError("7z non installé — apt-get install -y p7zip-full")

    dest = settings.boot_dir / os_slug / version_slug
    dest.mkdir(parents=True, exist_ok=True)

    try:
        specs_raw = json.loads(ot.extract_paths_json or "[]")
    except json.JSONDecodeError:
        specs_raw = []
    specs_raw = specs_raw if isinstance(specs_raw, list) else []
    unified = _normalize_specs(specs_raw)
    if not unified:
        raise ExtractionError(
            "Configuration d'extraction vide ou invalide (noms / motifs)."
        )

    basename_report: dict[str, list[str]] = {}

    def find_by_basename(root: Path, basename: str) -> list[Path]:
        low = basename.casefold()
        return sorted(
            (p for p in root.rglob("*") if p.is_file() and p.name.casefold() == low),
            key=lambda p: str(p),
        )

    if ot.extract_full_iso:
        proc = subprocess.run(
            [seven_z, "x", str(iso), f"-o{str(dest)}", "-y"],
            capture_output=True,
            text=True,
            timeout=settings.extract_timeout,
        )
        if proc.returncode not in (0, 1):
            raise ExtractionError(
                f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
            )
        for spec in unified:
            if spec.basename:
                hits = find_by_basename(dest, spec.basename)[: spec.max_n]
                basename_report[spec.basename] = [p.relative_to(dest).as_posix() for p in hits]
                if not hits:
                    logger.warning(
                        'Extraction complète : aucun fichier nommé "%s" dans l\'arborescence.',
                        spec.basename,
                    )
            else:
                logger.warning(
                    "Motif fnmatch legacy « %s » ignoré en extraction complète (arborescence déjà déployée sous %s). "
                    "Utilisez uniquement des noms de fichier pour les rapports, ou passez par l'extraction sélective.",
                    spec.pattern,
                    dest,
                )
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            proc = subprocess.run(
                [seven_z, "x", str(iso), f"-o{tmpp}", "-y"],
                capture_output=True,
                text=True,
                timeout=settings.extract_timeout,
            )
            if proc.returncode not in (0, 1):
                raise ExtractionError(
                    f"7z a échoué (code {proc.returncode}) :\n{proc.stderr[-2000:]}"
                )

            for spec in unified:
                if spec.pattern:
                    _legacy_pattern_copy_to_dest(tmpp, dest, spec.pattern, spec.max_n)
                    continue
                if not spec.basename:
                    continue

                hits = find_by_basename(tmpp, spec.basename)[: spec.max_n]

                if not hits:
                    basename_report[spec.basename] = []
                    logger.warning(
                        'Sélectif : aucune occurrence de « %s » dans l\'ISO.',
                        spec.basename,
                    )
                elif len(hits) == 1:
                    shutil.copy2(hits[0], dest / hits[0].name)
                    basename_report[spec.basename] = [hits[0].name]
                else:
                    for src in hits:
                        rel = src.relative_to(tmpp)
                        tgt = dest / rel
                        tgt.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, tgt)
                    basename_report[spec.basename] = [h.relative_to(tmpp).as_posix() for h in hits]
                logger.info(
                    'Sélectif « %s » : %d occurrence(s) — %s',
                    spec.basename,
                    len(hits),
                    basename_report.get(spec.basename),
                )

    _fix_permissions(dest)

    result: dict[str, object] = {}
    bt = (ot.boot_type or "linux").lower()
    if bt == "windows":
        try:
            extra = _find_windows_in_dest(dest, os_slug, version_slug)
            for k, v in extra.items():
                result.setdefault(k, v)
        except Exception as exc:
            logger.warning(
                "Détection Windows automatique après plan personnalisé : %s",
                exc,
            )
    elif bt in ("linux", "tools", "custom"):
        _fallback_kernel_initrd_in_dest(dest, os_slug, version_slug, result)

    filtered_report = {k: v for k, v in basename_report.items()}
    paths_out = {k: v for k, v in result.items()}
    paths_out["_meta"] = {"basename_report": filtered_report}

    logger.info(
        "Extraction plan OsType [%s/%s] boot=%s champs détectés=%s rapport noms=%s",
        os_slug,
        version_slug,
        bt,
        list(paths_out.keys()),
        filtered_report,
    )
    return paths_out


def _legacy_pattern_copy_to_dest(src_root: Path, dest: Path, pattern: str, max_n: int) -> None:
    cand: list[Path] = []
    for f in src_root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src_root).as_posix()
        pl = pattern.lower()
        rl = rel.lower()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rl, pl):
            cand.append(f)
    cand.sort(key=lambda p: str(p))
    if not cand:
        logger.warning("Motif sélectif : aucun fichier pour %s", pattern)
    taken = cand[:max_n]
    seen: set[str] = set()
    for src in taken:
        rel = src.relative_to(src_root)
        target = dest / rel
        key = str(target.resolve())
        if key in seen:
            continue
        seen.add(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        logger.info("Copié (motif) : %s", rel)


def _fallback_kernel_initrd_in_dest(dest: Path, os_slug: str, version_slug: str, result: dict) -> None:
    base = f"boot/{os_slug}/{version_slug}"
    if not result.get("kernel_path"):
        k = _find_in_dest(dest, _GENERIC_RULE["kernel"], "kernel")
        if k:
            result["kernel_path"] = f"{base}/{k.relative_to(dest).as_posix()}"
            logger.info("Fallback kernel : %s", k.relative_to(dest))
    if not result.get("initrd_path"):
        i = _find_in_dest(dest, _GENERIC_RULE["initrd"], "initrd")
        if i:
            result["initrd_path"] = f"{base}/{i.relative_to(dest).as_posix()}"
            logger.info("Fallback initrd : %s", i.relative_to(dest))
