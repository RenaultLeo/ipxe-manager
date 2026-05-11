"""
Scanne le répertoire configs/ et importe automatiquement les fichiers
de configuration non encore enregistrés en base.
"""
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import OsType, IsoVersion, AutoConfig
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

# ── Mapping OS slug → type de config par défaut ───────────────────────────────
OS_CONFIG_TYPE: dict[str, str] = {
    "windows":  "unattend",
    "winpe":    "unattend",
    "ubuntu":   "preseed",
    "debian":   "preseed",
    "centos":   "kickstart",
    "rocky":    "kickstart",
    "alma":     "kickstart",
    "fedora":   "kickstart",
    "proxmox":  "cloud-init",
    "esxi":     "custom",
    "tools":    "custom",
}

# Extension → type de config
EXT_TYPE: dict[str, str] = {
    ".cfg":   "preseed",
    ".ks":    "kickstart",
    ".xml":   "unattend",
    ".yaml":  "cloud-init",
    ".yml":   "cloud-init",
    ".txt":   "custom",
}

# Nom de fichier → type de config (priorité sur l'extension)
NAME_TYPE: dict[str, str] = {
    "preseed.cfg":    "preseed",
    "kickstart.cfg":  "kickstart",
    "ks.cfg":         "kickstart",
    "unattend.xml":   "unattend",
    "autounattend.xml": "unattend",
    "user-data":      "cloud-init",
    "cloud-config":   "cloud-init",
}


def default_config_type(os_slug: str) -> str:
    return OS_CONFIG_TYPE.get(os_slug, "custom")


def scan_and_import(db: Session) -> dict:
    """
    Parcourt settings.configs_dir à la recherche de fichiers config.
    Structure attendue : configs/{os_slug}/{version_slug}/{fichier}

    Retourne un dict {"imported": int, "skipped": int, "errors": list}.
    """
    results = {"imported": 0, "skipped": 0, "errors": []}
    configs_dir = settings.configs_dir
    if not configs_dir.exists():
        return results

    # Index des fichiers déjà en base (par file_path)
    existing = {ac.file_path for ac in db.query(AutoConfig).all() if ac.file_path}

    # Index des versions : {os_slug: {version_slug: IsoVersion}}
    version_index: dict[str, dict[str, IsoVersion]] = {}
    for v in db.query(IsoVersion).all():
        slug = v.os_type.slug
        vslug = slugify(v.version_label)
        version_index.setdefault(slug, {})[vslug] = v
        # Aussi indexer par str(id) pour les anciennes entrées
        version_index[slug][str(v.id)] = v

    for os_dir in sorted(configs_dir.iterdir()):
        if not os_dir.is_dir():
            continue
        os_slug = os_dir.name
        for ver_dir in sorted(os_dir.iterdir()):
            if not ver_dir.is_dir():
                continue
            version_key = ver_dir.name
            version = version_index.get(os_slug, {}).get(version_key)
            if not version:
                logger.debug("Pas de version trouvée pour %s/%s — ignoré", os_slug, version_key)
                results["skipped"] += 1
                continue

            for f in sorted(ver_dir.iterdir()):
                if not f.is_file():
                    continue
                rel = f"configs/{os_slug}/{version_key}/{f.name}"
                if rel in existing:
                    results["skipped"] += 1
                    continue

                # Détecter le type : nom > OS slug > extension > custom
                cfg_type = (
                    NAME_TYPE.get(f.name.lower())
                    or OS_CONFIG_TYPE.get(os_slug)
                    or EXT_TYPE.get(f.suffix.lower())
                    or "custom"
                )
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    ac = AutoConfig(
                        iso_version_id=version.id,
                        config_type=cfg_type,
                        label=f.stem,
                        content=content,
                        file_path=rel,
                    )
                    db.add(ac)
                    db.flush()
                    existing.add(rel)
                    results["imported"] += 1
                    logger.info("Config importée : %s (%s)", rel, cfg_type)
                except Exception as exc:
                    msg = f"{rel}: {exc}"
                    results["errors"].append(msg)
                    logger.exception("Erreur import config %s", rel)

    db.commit()
    return results
