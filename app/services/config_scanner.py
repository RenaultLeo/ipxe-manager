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
from app.services.autoconfig_label import (
    label_from_ubuntu_cloud_slug,
    next_autoconfig_menu_label,
)

logger = logging.getLogger(__name__)

UBUNTU_CLOUD_BUNDLE_PREFIX = "conf-cloudInit-"

# ── Mapping OS slug → type de config par défaut ───────────────────────────────
OS_CONFIG_TYPE: dict[str, str] = {
    "windows":  "unattend",
    "winpe":    "unattend",
    "ubuntu":   "cloud-init",       # user-data / meta-data (autoinstall)
    "debian":   "preseed",
    "centos":   "kickstart",
    "rocky":    "kickstart",
    "alma":     "kickstart",
    "fedora":   "kickstart",
    "proxmox":  "proxmox-answer",   # answer.toml
    "esxi":     "kickstart",        # ks.cfg
    "alpine":   "alpine-answer",    # answers / apkovl
    "tools":    "custom",
}

# ── Config forcée pour les OS built-in ────────────────────────────────────────
# Structure : {slug: {"type", "filenames", "description", "ext", "multi_file"}}
# multi_file=True  → l'utilisateur choisit quel fichier créer parmi `filenames`
# multi_file=False → toujours filenames[0]
FORCED_CONFIGS: dict[str, dict] = {
    "windows": {
        "type":        "unattend",
        "filenames":   ["autounattend.xml", "unattend.xml"],
        "description": "autounattend.xml (boot automatique) ou unattend.xml (post-boot)",
        "ext":         "xml",
        "multi_file":  True,
    },
    "winpe": {
        "type":        "unattend",
        "filenames":   ["autounattend.xml", "unattend.xml"],
        "description": "autounattend.xml ou unattend.xml",
        "ext":         "xml",
        "multi_file":  True,
    },
    "debian": {
        "type":        "preseed",
        "filenames":   ["preseed.cfg"],
        "description": "preseed.cfg",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "ubuntu": {
        "type":         "cloud-init",
        # Un seul dossier conf-cloudInit-<nom> avec user-data + meta-data (plus de multi_file)
        "description":  "Autoinstall — dossier conf-cloudInit-… avec user-data et meta-data",
        "ext":          "",
        "multi_file":   False,
        "ubuntu_bundle": True,
    },
    "centos": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "rocky": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "alma": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "fedora": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "proxmox": {
        "type":        "proxmox-answer",
        "filenames":   ["answer.toml"],
        "description": "answer.toml (Proxmox automated install)",
        "ext":         "toml",
        "multi_file":  False,
    },
    "esxi": {
        "type":        "kickstart",
        "filenames":   ["ks.cfg"],
        "description": "ks.cfg (ESXi kickstart)",
        "ext":         "cfg",
        "multi_file":  False,
    },
    "alpine": {
        "type":        "alpine-answer",
        "filenames":   ["answers", "alpine.apkovl.tar.gz"],
        "description": "answers (réponses setup-alpine) ou alpine.apkovl.tar.gz (overlay)",
        "ext":         "",
        "multi_file":  True,
    },
}

# Extension → type de config
EXT_TYPE: dict[str, str] = {
    ".cfg":   "preseed",
    ".ks":    "kickstart",
    ".xml":   "unattend",
    ".yaml":  "cloud-init",
    ".yml":   "cloud-init",
    ".toml":  "proxmox-answer",
    ".txt":   "custom",
}

# Nom de fichier → type de config (priorité sur l'extension)
NAME_TYPE: dict[str, str] = {
    # Windows / WinPE
    "autounattend.xml":       "unattend",
    "unattend.xml":           "unattend",
    # Debian
    "preseed.cfg":            "preseed",
    # Ubuntu (cloud-init / autoinstall)
    "user-data":              "cloud-init",
    "meta-data":              "cloud-init",
    "cloud-config":           "cloud-init",
    # RHEL family (CentOS, Rocky, Alma, Fedora, ESXi)
    "ks.cfg":                 "kickstart",
    "kickstart.cfg":          "kickstart",
    # Proxmox VE
    "answer.toml":            "proxmox-answer",
    # Alpine Linux
    "answers":                "alpine-answer",
    "alpine.apkovl.tar.gz":  "alpine-answer",
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
        if os_slug in ("fedora", "rocky", "alma", "centos"):
            return f"inst.ks={url}"
        else:  # esxi…
            return f"ks={url}"
    elif config_type == "cloud-init":
        # Ubuntu autoinstall (nocloud-net) — URL du dossier seed, toujours avec / final
        base_url = url.rstrip("/") + "/"
        return (
            f"autoinstall ds=nocloud-net;s={base_url} cloud-config-url=/dev/null"
        )
    elif config_type == "unattend":
        return ""   # injecté comme initrd dans wimboot (Windows)
    elif config_type == "proxmox-answer":
        # Menu autoinstall : initrd proxmox-netboot-autoinstall.iso (prepare-iso officiel)
        return "proxmox-start-auto-installer"
    elif config_type == "alpine-answer":
        return f"ANSWERSFILE={url}"
    elif config_type == "custom":
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
    # Compteur d’imports par version pour libellés config 1, 2, …
    import_pending: dict[int, int] = {}

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

            # Ubuntu : dossiers conf-cloudInit-<slug>/ avec user-data + meta-data
            if os_slug == "ubuntu":
                for sub in sorted(ver_dir.iterdir()):
                    if not sub.is_dir() or not sub.name.startswith(UBUNTU_CLOUD_BUNDLE_PREFIX):
                        continue
                    slug = sub.name[len(UBUNTU_CLOUD_BUNDLE_PREFIX) :]
                    ud_path = sub / "user-data"
                    md_path = sub / "meta-data"
                    if not ud_path.is_file() or not md_path.is_file():
                        continue
                    rel_bundle = f"configs/{os_slug}/{version_key}/{sub.name}"
                    if rel_bundle in existing:
                        results["skipped"] += 1
                        continue
                    try:
                        ud_txt = ud_path.read_text(encoding="utf-8", errors="replace")
                        md_txt = md_path.read_text(encoding="utf-8", errors="replace")
                        menu_label = label_from_ubuntu_cloud_slug(slug)
                        if menu_label.lower() in ("user-data", "meta-data"):
                            menu_label = next_autoconfig_menu_label(
                                db, version.id, extra=import_pending.get(version.id, 0)
                            )
                        import_pending[version.id] = import_pending.get(version.id, 0) + 1
                        ac = AutoConfig(
                            iso_version_id=version.id,
                            config_type="cloud-init",
                            label=menu_label,
                            content=ud_txt,
                            meta_data_content=md_txt,
                            ubuntu_cloud_slug=slug,
                            file_path=rel_bundle,
                        )
                        db.add(ac)
                        db.flush()
                        existing.add(rel_bundle)
                        results["imported"] += 1
                        logger.info(
                            "Config bundle importée : %s (Ubuntu autoinstall)", rel_bundle
                        )
                    except Exception as exc:
                        msg = f"{rel_bundle}: {exc}"
                        results["errors"].append(msg)
                        logger.exception("Erreur import bundle %s", rel_bundle)

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
                    stem_l = f.stem.lower()
                    if stem_l in ("user-data", "meta-data", "cloud-config"):
                        menu_label = next_autoconfig_menu_label(
                            db, version.id, extra=import_pending.get(version.id, 0)
                        )
                    else:
                        menu_label = f.stem
                    import_pending[version.id] = import_pending.get(version.id, 0) + 1
                    ac = AutoConfig(
                        iso_version_id=version.id,
                        config_type=cfg_type,
                        label=menu_label,
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
