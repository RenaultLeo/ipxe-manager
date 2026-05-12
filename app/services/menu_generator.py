"""
Generates all .ipxe menu files from the database and Jinja2 templates.
"""
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import OsType, IsoVersion, BootEntry
from app.services.config_scanner import config_boot_arg

logger = logging.getLogger(__name__)

TMPL_DIR = Path(__file__).parent.parent / "ipxe_templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TMPL_DIR)),
        keep_trailing_newline=True,
    )


def _build_entry(v: IsoVersion, os_type: OsType) -> dict:
    """Construit le dict d'une version pour les templates Jinja2."""
    be = v.boot_entry
    return {
        "id":           v.id,
        "label":        f"{os_type.label} {v.version_label}",
        "kernel":       _http(be.kernel_path)      if be and be.kernel_path      else "",
        "initrd":       _http(be.initrd_path)      if be and be.initrd_path      else "",
        "boot_wim":     _http(be.boot_wim_path)    if be and be.boot_wim_path    else "",
        "bcd":          _http(be.bcd_path)         if be and be.bcd_path         else "",
        "boot_sdi":     _http(be.boot_sdi_path)    if be and be.boot_sdi_path    else "",
        "unattend_url": _find_unattend_url(v, os_type),
        "bootmgr":      _http(be.bootmgr_path)     if be and be.bootmgr_path     else "",
        "custom_ipxe":  _http(be.custom_ipxe_path) if be and be.custom_ipxe_path else "",
        "modloop":      _http(be.modloop_path)     if be and be.modloop_path     else "",
        # Pour Alpine : injecter modloop= dans les kernel args automatiquement
        "kernel_args":  _build_kernel_args(be, os_type.slug),
        "boot_type":    os_type.boot_type or "linux",
        "autoconfigs": [
            {
                "id":       ac.id,
                "label":    ac.label or ac.config_type,
                "url":      _http(ac.file_path) if ac.file_path else "",
                "type":     ac.config_type,
                "boot_arg": config_boot_arg(
                    ac.config_type,
                    os_type.slug,
                    _http(ac.file_path) if ac.file_path else "",
                ),
            }
            for ac in v.autoconfigs
        ],
    }


def regenerate_all(db: Session) -> list[str]:
    """Regenerate every menu file. Returns list of written file paths."""
    settings.menus_dir.mkdir(parents=True, exist_ok=True)
    env = _jinja_env()
    written: list[str] = []

    os_types = db.query(OsType).all()

    # Per-OS sub-menus
    for os_type in os_types:
        try:
            versions = (
                db.query(IsoVersion)
                .filter(
                    IsoVersion.os_type_id == os_type.id,
                    IsoVersion.status == "ready",
                )
                .all()
            )

            # Séparer : versions standard vs versions avec script iPXE custom
            standard_entries = []
            custom_entries   = []
            for v in versions:
                entry = _build_entry(v, os_type)
                if entry["custom_ipxe"]:
                    custom_entries.append(entry)
                else:
                    standard_entries.append(entry)

            has_autres = len(custom_entries) > 0

            # ── Menu principal de l'OS ──────────────────────────────────────
            tmpl_name = "linux.ipxe.j2" if (os_type.boot_type or "linux") == "linux" else "windows.ipxe.j2"
            if not (TMPL_DIR / tmpl_name).exists():
                tmpl_name = "linux.ipxe.j2"

            tmpl = env.get_template(tmpl_name)
            content = tmpl.render(
                os_type=os_type,
                entries=standard_entries,
                has_autres=has_autres,
                server_url=settings.server_base_url,
            )
            out = settings.menus_dir / f"{os_type.slug}.ipxe"
            out.write_text(content, encoding="utf-8")
            written.append(str(out))

            # ── Sous-menu "Autres" (scripts iPXE custom) ────────────────────
            if has_autres:
                tmpl_autres = env.get_template("autres.ipxe.j2")
                content_autres = tmpl_autres.render(
                    os_type=os_type,
                    entries=custom_entries,
                    server_url=settings.server_base_url,
                )
                out_autres = settings.menus_dir / f"{os_type.slug}_autres.ipxe"
                out_autres.write_text(content_autres, encoding="utf-8")
                written.append(str(out_autres))
                logger.info("Menu Autres généré : %s (%d entrées)", out_autres, len(custom_entries))
            else:
                # Supprimer l'ancien _autres.ipxe s'il n'y a plus de versions custom
                old = settings.menus_dir / f"{os_type.slug}_autres.ipxe"
                old.unlink(missing_ok=True)

        except Exception:
            logger.exception("Erreur génération menu pour OS type '%s'", os_type.slug)

    # Central menu
    tmpl = env.get_template("menu.ipxe.j2")
    content = tmpl.render(
        os_types=os_types,
        server_url=settings.server_base_url,
    )
    out = settings.menus_dir / "menu.ipxe"
    out.write_text(content, encoding="utf-8")
    written.append(str(out))

    return written


def _build_kernel_args(be, os_slug: str) -> str:
    """Assemble les kernel args, en ajoutant modloop= pour Alpine."""
    args = be.kernel_args if be and be.kernel_args else ""
    if os_slug == "alpine" and be and be.modloop_path:
        modloop_url = _http(be.modloop_path)
        if f"modloop=" not in args:
            args = f"{args} modloop={modloop_url}".strip()
    return args


def _find_unattend_url(v: IsoVersion, os_type: OsType) -> str:
    """Cherche autounattend.xml à la racine du dossier boot de la version."""
    if os_type.boot_type != "windows":
        return ""
    from app.services.slugify import slugify
    version_slug = slugify(v.version_label)
    path = settings.boot_dir / os_type.slug / version_slug / "autounattend.xml"
    if path.exists():
        return _http(f"boot/{os_type.slug}/{version_slug}/autounattend.xml")
    return ""


def _http(relative_path: str | None) -> str:
    if not relative_path:
        return ""
    clean = relative_path.lstrip("/")
    return f"{settings.server_base_url}/{clean}"
