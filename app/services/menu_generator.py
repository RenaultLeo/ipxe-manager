"""
Generates all .ipxe menu files from the database and Jinja2 templates.
"""
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import OsType, IsoVersion, BootEntry

logger = logging.getLogger(__name__)

TMPL_DIR = Path(__file__).parent.parent / "ipxe_templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TMPL_DIR)),
        keep_trailing_newline=True,
    )


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
            entries = []
            for v in versions:
                be = v.boot_entry
                entries.append(
                    {
                        "id": v.id,
                        "label": f"{os_type.label} {v.version_label}",
                        "kernel":      _http(be.kernel_path)   if be and be.kernel_path   else "",
                        "initrd":      _http(be.initrd_path)   if be and be.initrd_path   else "",
                        "boot_wim":    _http(be.boot_wim_path) if be and be.boot_wim_path else "",
                        "bcd":         _http(be.bcd_path)      if be and be.bcd_path      else "",
                        "boot_sdi":    _http(be.boot_sdi_path) if be and be.boot_sdi_path else "",
                        "bootmgr":     _http(be.bootmgr_path)  if be and be.bootmgr_path  else "",
                        "kernel_args": be.kernel_args if be else "",
                        "boot_type":   os_type.boot_type or "linux",
                        "autoconfigs": [
                            {
                                "id": ac.id,
                                "label": ac.label or ac.config_type,
                                "url": _http(ac.file_path) if ac.file_path else "",
                            }
                            for ac in v.autoconfigs
                        ],
                    }
                )

            tmpl_name = "linux.ipxe.j2" if (os_type.boot_type or "linux") == "linux" else "windows.ipxe.j2"
            if not (TMPL_DIR / tmpl_name).exists():
                tmpl_name = "linux.ipxe.j2"

            tmpl = env.get_template(tmpl_name)
            content = tmpl.render(
                os_type=os_type,
                entries=entries,
                server_url=settings.server_base_url,
            )
            out = settings.menus_dir / f"{os_type.slug}.ipxe"
            out.write_text(content, encoding="utf-8")
            written.append(str(out))
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


def _http(relative_path: str | None) -> str:
    if not relative_path:
        return ""
    # Paths stored relative to http_root; convert to URL
    clean = relative_path.lstrip("/")
    return f"{settings.server_base_url}/{clean}"
