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
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import settings
from app.models.models import OsType
from app.services.iso_extractor import (
    ExtractionError,
    DISTRO_RULES,
    _GENERIC_RULE,
    _extract_esxi_from_full_dest,
    _find_in_dest,
    _find_proxmox_in_dest,
    publish_proxmox_netboot_iso,
    _find_windows_in_dest,
    _fix_permissions,
    extract_iso_archive,
)

logger = logging.getLogger(__name__)

# Extraction complète « intégrée » dans ``iso_extractor.extract_iso`` (7z + détection vmlinuz/initrd).
# Si l’admin coche « extraction complète » sur le type d’OS sans liste de noms, on évite l’erreur
# « Configuration vide » et on délègue au moteur intégré (Fedora / EL / Ubuntu / Windows comme en seed).
_BUILTIN_FULL_ISO_SLUGS = frozenset(
    {
        "windows",
        "winpe",
        "ubuntu",
        "debian",
        "rocky",
        "alma",
        "centos",
        "fedora",
        "proxmox",
    }
)


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


def _slot_terms_from_specs_raw(specs_raw: list) -> list[str]:
    """Même ordre / libellés que ``linux_manual_*`` dans le formulaire d'upload ISO."""
    out: list[str] = []
    if not isinstance(specs_raw, list):
        return out
    for row in specs_raw:
        if not isinstance(row, dict):
            continue
        fn = str(row.get("filename") or row.get("name") or "").strip()
        pat = str(row.get("pattern") or "").strip()
        if fn:
            bn = PurePosixPath(fn.replace("\\", "/")).name
            if bn:
                out.append(bn)
        elif pat:
            out.append(pat)
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

    if not (
        shutil.which("xorriso")
        or shutil.which("bsdtar")
        or shutil.which("7z")
        or shutil.which("7za")
    ):
        raise ExtractionError(
            "Aucun outil d'extraction ISO — apt install xorriso p7zip-full libarchive-tools"
        )

    dest = settings.boot_dir / os_slug / version_slug
    dest.mkdir(parents=True, exist_ok=True)

    try:
        specs_raw = json.loads(ot.extract_paths_json or "[]")
    except json.JSONDecodeError:
        specs_raw = []
    specs_raw = specs_raw if isinstance(specs_raw, list) else []
    unified = _normalize_specs(specs_raw)
    bt_early = (ot.boot_type or "linux").lower()
    esxi_full_skip_specs = (
        getattr(ot, "extract_full_iso", False)
        and bt_early == "esxi"
        and not unified
    )
    if not unified and not esxi_full_skip_specs:
        slug_lc = (os_slug or "").strip().lower()
        if getattr(ot, "extract_full_iso", False) and slug_lc in _BUILTIN_FULL_ISO_SLUGS:
            logger.info(
                'Extraction : slug « %s », extract_full_iso sans noms dans Paramètres — '
                "recours à iso_extractor (règles intégrées).",
                slug_lc,
            )
            return None
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
        extract_iso_archive(iso, dest)
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
            extract_iso_archive(iso, tmpp)

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
        base = f"boot/{os_slug}/{version_slug}"
        _assign_ordered_linux_slots_from_plan(
            os_slug, specs_raw, unified, basename_report, base, result
        )
        _fallback_kernel_initrd_in_dest(dest, os_slug, version_slug, result)
        if os_slug == "proxmox" and (
            not result.get("kernel_path") or not result.get("initrd_path")
        ):
            try:
                pve = _find_proxmox_in_dest(
                    dest, os_slug, version_slug, DISTRO_RULES.get("proxmox", _GENERIC_RULE)
                )
                result.update(pve)
            except Exception as exc:
                logger.warning("Détection Proxmox après plan personnalisé : %s", exc)

    elif bt == "esxi":
        if not ot.extract_full_iso:
            raise ExtractionError(
                "ESXi : activer « extraction ISO complète » pour ce type de système ; "
                "boot.cfg / mboot et les modules VMware nécessitent l’arborescence entière sous HTTP."
            )
        esxi_paths = _extract_esxi_from_full_dest(dest, os_slug, version_slug)
        result.update(esxi_paths)

    if os_slug == "proxmox":
        publish_proxmox_netboot_iso(Path(iso_path), dest)

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


def _assign_ordered_linux_slots_from_plan(
    os_slug: str,
    specs_raw: list,
    unified: list[_UnifiedSpec],
    basename_report: dict[str, list[str]],
    base: str,
    result: dict,
) -> None:
    """
    Mappe les entrées du plan (ordre dans ``extract_paths_json``) comme sur l’upload manuel :
    slot 0 → kernel_path, slot 1 → initrd_path, Alpine+modloop → modloop_path, le reste → extra_linux_paths.

    Absent ces slots, le fallback générique ``vmlinuz`` / ``initrd`` ne voit pas par ex. ``bzImage`` (NixOS)
    alors que le fichier a bien été copié depuis l’ISO selon le plan.
    """
    slot_terms = _slot_terms_from_specs_raw(specs_raw)
    if len(slot_terms) != len(unified):
        logger.warning(
            "Plan extraction [%s]: %s termes formulaire ≠ %s entrées normalisées — alignement par index minimal.",
            os_slug,
            len(slot_terms),
            len(unified),
        )
    extras: list[dict] = []
    for idx in range(min(len(slot_terms), len(unified))):
        spec = unified[idx]
        term = slot_terms[idx]
        if spec.pattern:
            continue
        if not (spec.basename or "").strip():
            continue
        rels = basename_report.get(spec.basename) or []
        if not rels:
            continue
        rel_first = rels[0].replace("\\", "/")
        http_rel = f"{base}/{rel_first}"

        low_term = term.lower()
        basename_only = PurePosixPath(http_rel.replace("\\", "/")).name.casefold()

        if idx == 0:
            result["kernel_path"] = http_rel
            logger.info("Plan slot %s kernel : %s", idx, http_rel)
        elif idx == 1:
            # Proxmox : 2e nom « vmlinuz » dans le plan = autre noyau, pas l'initrd
            from app.services.iso_extractor import _PROXMOX_EXTRA_KERNEL_BASENAMES

            if os_slug == "proxmox" and basename_only in _PROXMOX_EXTRA_KERNEL_BASENAMES:
                if not result.get("kernel_path"):
                    result["kernel_path"] = http_rel
                    logger.info(
                        "Plan slot %s kernel Proxmox (secours, pas initrd) : %s",
                        idx,
                        http_rel,
                    )
                else:
                    extras.append({"basename": term, "path": http_rel})
                    logger.info(
                        "Plan slot %s noyau Proxmox supplémentaire : %s",
                        idx,
                        http_rel,
                    )
                continue
            result["initrd_path"] = http_rel
            logger.info("Plan slot %s initrd : %s", idx, http_rel)
        elif os_slug == "alpine" and (
            "modloop" in low_term or basename_only.startswith("modloop")
        ):
            if not result.get("modloop_path"):
                result["modloop_path"] = http_rel
                logger.info("Plan slot %s modloop : %s", idx, http_rel)
            else:
                extras.append({"basename": term, "path": http_rel})
        else:
            extras.append({"basename": term, "path": http_rel})
            logger.info("Plan slot %s extra Linux (« %s ») : %s", idx, term, http_rel)

    result["extra_linux_paths"] = extras


def _fallback_kernel_initrd_in_dest(dest: Path, os_slug: str, version_slug: str, result: dict) -> None:
    base = f"boot/{os_slug}/{version_slug}"
    if not result.get("kernel_path"):
        if os_slug == "proxmox":
            try:
                pve = _find_proxmox_in_dest(
                    dest, os_slug, version_slug, DISTRO_RULES.get("proxmox", _GENERIC_RULE)
                )
                if pve.get("kernel_path"):
                    result["kernel_path"] = pve["kernel_path"]
                    logger.info("Fallback kernel Proxmox : %s", pve["kernel_path"])
            except ExtractionError:
                pass
        else:
            k = _find_in_dest(dest, _GENERIC_RULE["kernel"], "kernel")
            if k:
                result["kernel_path"] = f"{base}/{k.relative_to(dest).as_posix()}"
                logger.info("Fallback kernel : %s", k.relative_to(dest))
    if not result.get("initrd_path"):
        initrd_names = (
            (DISTRO_RULES.get("proxmox") or {}).get("initrd")
            if os_slug == "proxmox"
            else _GENERIC_RULE["initrd"]
        )
        i = _find_in_dest(dest, initrd_names or ["initrd.img"], "initrd")
        if i:
            result["initrd_path"] = f"{base}/{i.relative_to(dest).as_posix()}"
            logger.info("Fallback initrd : %s", i.relative_to(dest))
