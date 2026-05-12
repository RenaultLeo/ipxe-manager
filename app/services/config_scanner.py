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
    "ubuntu":   "cloud-init",   # user-data / meta-data (autoinstall)
    "debian":   "preseed",
    "centos":   "kickstart",
    "rocky":    "kickstart",
    "alma":     "kickstart",
    "fedora":   "kickstart",
    "proxmox":  "custom",       # answer.toml
    "esxi":     "kickstart",    # ks.cfg
    "alpine":   "custom",       # answers / apkovl
    "tools":    "custom",
}

# ── Config forcée pour les OS built-in ────────────────────────────────────────
# Structure : {slug: {"type": str, "filenames": [str, ...], "description": str}}
# filenames[0] = nom canonique utilisé à la création
FORCED_CONFIGS: dict[str, dict] = {
    "windows": {
        "type":        "unattend",
        "filenames":   ["autounattend.xml", "unattend.xml"],
        "description": "autounattend.xml (boot) ou unattend.xml (post-boot)",
        "ext":         "xml",
    },
    "winpe": {
        "type":        "unattend",
        "filenames":   ["autounattend.xml", "unattend.xml"],
        "description": "autounattend.xml ou unattend.xml",
        "ext":         "xml",
    },
    "debian": {
        "type":        "preseed",
        "filenames":   ["preseed.cfg"],
        "description": "preseed.cfg",
        "ext":         "cfg",
    },
    "ubuntu": {
        "type":        "cloud-init",
        "filenames":   ["user-data", "meta-data"],
        "description": "user-data + meta-data (autoinstall)",
        "ext":         "",          # pas d'extension
    },
    "centos": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
    },
    "rocky": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
    },
    "alma": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
    },
    "fedora": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
    },
    "proxmox": {
        "type":        "custom",
        "filenames":   ["answer.toml"],
        "description": "answer.toml (Proxmox automated install)",
        "ext":         "toml",
    },
    "esxi": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg (ESXi kickstart)",
        "ext":         "cfg",
    },
    "alpine": {
        "type":        "custom",
        "filenames":   ["answers", "alpine.apkovl.tar.gz"],
        "description": "answers (setup-alpine) ou alpine.apkovl.tar.gz",
        "ext":         "",
    },
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
    # Windows / WinPE
    "autounattend.xml":  "unattend",
    "unattend.xml":      "unattend",
    # Debian
    "preseed.cfg":       "preseed",
    # Ubuntu (cloud-init / autoinstall)
    "user-data":         "cloud-init",
    "meta-data":         "cloud-init",
    "cloud-config":      "cloud-init",
    # RHEL family (CentOS, Rocky, Alma, Fedora, ESXi)
    "ks.cfg":            "kickstart",
    "kickstart.cfg":     "kickstart",
    # Proxmox VE
    "answer.toml":       "custom",
    # Alpine Linux
    "answers":           "custom",
    "alpine.apkovl.tar.gz": "custom",
}


def default_config_type(os_slug: str) -> str:
    return OS_CONFIG_TYPE.get(os_slug, "custom")


def config_boot_arg(config_type: str, os_slug: str, url: str) -> str:
    """
    Retourne l'argument kernel iPXE à ajouter pour déclencher l'install automatique.
    Retourne "" pour unattend (Windows) — géré via wimboot initrd dans le template.
    """
    if config_type == "preseed":
        # Debian preseed
        return f"auto=true priority=critical preseed/url={url}"
    elif config_type == "kickstart":
        if os_slug in ("fedora", "rocky", "alma"):
            return f"inst.ks={url}"
        else:  # centos, esxi…
            return f"ks={url}"
    elif config_type == "cloud-init":
        # Ubuntu autoinstall : pointe sur le dossier contenant user-data + meta-data
        # url doit se terminer par / (le dossier, pas le fichier)
        base_url = url.rsplit("/", 1)[0] + "/" if not url.endswith("/") else url
        return f"autoinstall ds=nocloud-net;s={base_url}"
    elif config_type == "unattend":
        return ""   # injecté comme initrd dans wimboot (Windows)
    elif config_type == "custom":
        # Proxmox answer.toml : passé via le paramètre proxmox-installer
        if os_slug == "proxmox":
            return f"proxmox-installer.answer-file={url}"
        # Alpine answers : passé à alpine-conf
        if os_slug == "alpine":
            return f"ANSWERSFILE={url}"
        return f"url={url}"
    else:
        return f"url={url}"


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
