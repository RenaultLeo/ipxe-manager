"""
Scanne le répertoire boot/ et met à jour les BootEntry en DB
avec les fichiers déjà présents sur le disque (BCD, boot.sdi, boot.wim, vmlinuz…).
Utile quand les fichiers ont été copiés/extraits sans passer par l'interface.
"""
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import OsType, IsoVersion, BootEntry
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

# Fichiers Windows connus → champ BootEntry
WIN_FILES = {
    "bcd":          "bcd_path",
    "boot.sdi":     "boot_sdi_path",
    "boot.wim":     "boot_wim_path",
    "bootmgr.efi":  "bootmgr_path",
    "bootmgfw.efi": "bootmgr_path",
}

# Préfixes Linux → champ BootEntry
LINUX_KERNEL_PREFIXES = ("vmlinuz", "vmlinux", "linux", "kernel")
LINUX_INITRD_PREFIXES  = ("initrd", "initramfs")


def scan_and_register(db: Session) -> dict:
    """
    Parcourt boot/{os_slug}/{version_slug}/ et enregistre les fichiers en DB.
    Retourne {"updated": int, "skipped": int}.
    """
    result = {"updated": 0, "skipped": 0, "errors": []}
    boot_dir = settings.boot_dir
    if not boot_dir.exists():
        return result

    # Index des versions : {os_slug: {version_slug: version, str(id): version}}
    version_index: dict[str, dict[str, IsoVersion]] = {}
    for v in db.query(IsoVersion).all():
        slug = v.os_type.slug
        vslug = slugify(v.version_label)
        version_index.setdefault(slug, {})[vslug] = v
        version_index[slug][str(v.id)] = v   # compat anciens dossiers numériques

    for os_dir in sorted(boot_dir.iterdir()):
        if not os_dir.is_dir():
            continue
        os_slug = os_dir.name

        # Récupérer le boot_type de cet OS
        os_type = db.query(OsType).filter(OsType.slug == os_slug).first()
        is_windows = os_type and os_type.boot_type == "windows"

        for ver_dir in sorted(os_dir.iterdir()):
            if not ver_dir.is_dir():
                continue
            version_key = ver_dir.name
            version = version_index.get(os_slug, {}).get(version_key)
            if not version:
                logger.debug("Pas de version pour %s/%s — ignoré", os_slug, version_key)
                result["skipped"] += 1
                continue

            # Récupérer ou créer le BootEntry
            be = version.boot_entry
            if not be:
                be = BootEntry(iso_version_id=version.id)
                db.add(be)
                db.flush()

            changed = False
            base = f"boot/{os_slug}/{version_key}"

            for f in sorted(ver_dir.iterdir()):
                if not f.is_file():
                    continue
                fname_lower = f.name.lower()
                rel = f"{base}/{f.name}"

                if is_windows:
                    field = WIN_FILES.get(fname_lower)
                    if field and not getattr(be, field):
                        setattr(be, field, rel)
                        changed = True
                        logger.info("Registré %s → %s.%s", rel, "BootEntry", field)
                else:
                    # vmlinuz*
                    if any(fname_lower == p or fname_lower.startswith(p + "-")
                           for p in LINUX_KERNEL_PREFIXES):
                        if not be.kernel_path:
                            be.kernel_path = rel
                            changed = True
                            logger.info("Kernel registré : %s", rel)
                    # initrd*
                    elif any(fname_lower == p or fname_lower.startswith(p)
                             for p in LINUX_INITRD_PREFIXES):
                        if not be.initrd_path:
                            be.initrd_path = rel
                            changed = True
                            logger.info("Initrd registré : %s", rel)

            if changed:
                if version.status != "ready":
                    version.status = "ready"
                result["updated"] += 1
            else:
                result["skipped"] += 1

    db.commit()
    return result
