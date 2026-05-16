"""
Extraction ISO pilotée par la configuration enregistrée sur ``OsType`` (paramètres avancés).
Utilisé lorsque ``extract_full_iso`` est coché ou que ``extract_paths_json`` liste des motifs.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

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

# Rôle saisi dans le formulaire → clé du dict retourné par l'extracteur (cf. jobs.py)
ROLE_TO_RESULT_KEY: dict[str, str] = {
    "kernel": "kernel_path",
    "initrd": "initrd_path",
    "boot_wim": "boot_wim_path",
    "bcd": "bcd_path",
    "boot_sdi": "boot_sdi_path",
    "bootmgr": "bootmgr_path",
    "modloop": "modloop_path",
    "esxi_boot_cfg": "esxi_boot_cfg_path",
}


def uses_custom_extract_plan(ot: OsType) -> bool:
    """True si l'utilisateur a défini un plan personnalisé (hors comportement distro codé en dur)."""
    if getattr(ot, "extract_full_iso", False):
        return True
    try:
        raw = getattr(ot, "extract_paths_json", None) or "[]"
        data = json.loads(raw)
        return isinstance(data, list) and len(data) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def try_extract_with_plan(
    iso_path: str,
    ot: OsType,
    version_id: int,
    version_label: str,
) -> dict | None:
    """
    Lance l'extraction selon OsType.extract_* / ipxe_roles_json.
    Retourne None pour laisser le flux historique ``extract_iso`` (DISTRO_RULES).
    """
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
        specs = json.loads(ot.extract_paths_json or "[]")
    except json.JSONDecodeError:
        specs = []
    specs = specs if isinstance(specs, list) else []

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
            _selective_copy_patterns(tmpp, dest, specs)

    _fix_permissions(dest)

    try:
        roles = json.loads(ot.ipxe_roles_json or "[]")
    except json.JSONDecodeError:
        roles = []
    roles = roles if isinstance(roles, list) else []

    result: dict = {}
    if roles:
        result.update(_map_roles_under_dest(dest, os_slug, version_slug, roles))

    bt = (ot.boot_type or "linux").lower()
    if bt == "windows":
        extra = _find_windows_in_dest(dest, os_slug, version_slug)
        for k, v in extra.items():
            result.setdefault(k, v)
    elif bt in ("linux", "tools", "custom"):
        _fallback_kernel_initrd_in_dest(dest, os_slug, version_slug, result)

    # Garde ESXi hors plan personnalisé pour l’instant (règle codée en dur très spécifique)
    if not result:
        raise ExtractionError(
            "Extraction pilotée par le type d’OS : aucun fichier de boot trouvé après application du plan "
            "(vérifier les motifs et les rôles iPXE)."
        )

    logger.info("Extraction plan OsType terminée [%s/%s] : %s", os_slug, version_slug, list(result.keys()))
    return result


def _selective_copy_patterns(src_root: Path, dest: Path, specs: list[dict]) -> None:
    """Copie depuis l’ISO décompressée uniquement les fichiers correspondants (fnmatch POSIX relatif)."""
    if not specs:
        raise ExtractionError(
            "Liste de motifs d’extraction vide : cochez « extraction complète » ou ajoutez au moins un motif."
        )
    seen_dst: set[str] = set()
    for spec in specs:
        pattern = (spec.get("pattern") or "").strip() or "**/*"
        try:
            max_n = max(1, int(spec.get("max", 1)))
        except (TypeError, ValueError):
            max_n = 1
        candidates: list[Path] = []
        for f in src_root.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(src_root).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel.lower(), pattern.lower()):
                candidates.append(f)
        candidates.sort(key=lambda p: str(p))
        taken = candidates[:max_n]
        if not taken:
            logger.warning("Aucun fichier pour le motif %s", pattern)
        for src in taken:
            rel = src.relative_to(src_root)
            target = dest / rel
            key = str(target.resolve())
            if key in seen_dst:
                continue
            seen_dst.add(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            logger.info("Copié (sélectif) : %s", rel)


def _map_roles_under_dest(dest: Path, os_slug: str, version_slug: str, roles: list[dict]) -> dict:
    """Associe path_pattern → champs boot (ordre sort_order croissant)."""
    base = f"boot/{os_slug}/{version_slug}"
    out: dict = {}
    esxi_mod_basenames: list[str] = []
    used: set[str] = set()

    def rel_key(p: Path) -> str:
        return str(p.resolve())

    ordered = sorted(roles, key=lambda r: int(r.get("sort_order", 0) or 0))
    for row in ordered:
        role = (row.get("role") or "").strip().lower()
        pat = (row.get("path_pattern") or "").strip()
        if not role or not pat:
            continue
        hit = _first_unmatched_under_dest(dest, pat, used)
        if not hit:
            logger.warning("Rôle %s : aucun fichier pour pattern %s", role, pat)
            continue
        used.add(rel_key(hit))
        rel = hit.relative_to(dest).as_posix()
        url = f"{base}/{rel}"
        if role == "esxi_module":
            esxi_mod_basenames.append(hit.name)
            continue
        rkey = ROLE_TO_RESULT_KEY.get(role)
        if rkey:
            out[rkey] = url
        else:
            logger.warning("Rôle iPXE inconnu ignoré : %s", role)

    if esxi_mod_basenames:
        out["esxi_modules"] = json.dumps(esxi_mod_basenames)
    return out


def _first_unmatched_under_dest(dest: Path, pattern: str, used: set[str]) -> Path | None:
    files = sorted((p for p in dest.rglob("*") if p.is_file()), key=lambda p: str(p))
    for f in files:
        if str(f.resolve()) in used:
            continue
        rel = f.relative_to(dest).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel.lower(), pattern.lower()):
            return f
    return None


def _fallback_kernel_initrd_in_dest(dest: Path, os_slug: str, version_slug: str, result: dict) -> None:
    """Complète kernel/initrd depuis l’arborescence déjà extraite (règles génériques)."""
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
