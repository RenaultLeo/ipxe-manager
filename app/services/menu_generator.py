"""
Generates all .ipxe menu files from the database and Jinja2 templates.
"""
import logging
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.models import OsType, IsoVersion, BootEntry, RemoteChain
from app.services.config_scanner import config_boot_arg
from app.services.os_type_order import sort_os_types_for_ui
from app.services.slugify import slugify

logger = logging.getLogger(__name__)

TMPL_DIR = Path(__file__).parent.parent / "ipxe_templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TMPL_DIR)),
        keep_trailing_newline=True,
    )


def _boot_os_version_segment(be: BootEntry | None, os_slug: str) -> str | None:
    """
    Nom du dossier version sous http/boot/<os_slug>/, tel qu'enregistré après extraction
    (kernel_path / initrd_path). Aligne nfsroot= et la structure disque
    ``http/boot/<os>/<version>`` (ex. ubuntu24.04) sans dépendre du libellé affiché.
    """
    if not be:
        return None
    for rel in (be.kernel_path, be.initrd_path):
        if not rel:
            continue
        parts = rel.replace("\\", "/").lstrip("/").split("/")
        if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == os_slug.lower():
            return parts[2]
    return None


def _build_entry(v: IsoVersion, os_type: OsType, cfg: Settings) -> dict:
    """Construit le dict d'une version pour les templates Jinja2."""
    be = v.boot_entry
    version_slug = _boot_os_version_segment(be, os_type.slug) or slugify(v.version_label)
    nfs_pair = cfg.ubuntu_nfsroot_pair(os_type.slug, version_slug)
    if os_type.slug.lower() == "ubuntu" and cfg.ubuntu_nfs_enabled and not nfs_pair:
        logger.warning(
            "Ubuntu NFS: UBUNTU_NFS_ENABLED mais nfsroot vide pour \"%s\". "
            "Vérifier HTTP_ROOT et boot/ubuntu/<slug> sur le serveur, puis régénérer les menus.",
            v.version_label,
        )

    def h(rel: str | None) -> str:
        return _http(rel, cfg)

    return {
        "id":           v.id,
        "label":        f"{os_type.label} {v.version_label}",
        "kernel":       h(be.kernel_path) if be and be.kernel_path else "",
        "initrd":       h(be.initrd_path) if be and be.initrd_path else "",
        "boot_wim":     h(be.boot_wim_path) if be and be.boot_wim_path else "",
        "bcd":          h(be.bcd_path) if be and be.bcd_path else "",
        "boot_sdi":     h(be.boot_sdi_path) if be and be.boot_sdi_path else "",
        "unattend_url": _find_unattend_url(v, os_type, cfg),
        "bootmgr":      h(be.bootmgr_path) if be and be.bootmgr_path else "",
        "custom_ipxe":  h(be.custom_ipxe_path) if be and be.custom_ipxe_path else "",
        "modloop":      h(be.modloop_path) if be and be.modloop_path else "",
        # Pour Alpine / Ubuntu : injections dans kernel_args ci-dessous
        "kernel_args": _build_kernel_args(be, os_type.slug, cfg, nfsroot_pair=nfs_pair),
        "boot_type":   os_type.boot_type or "linux",
        "autoconfigs": [
            {
                "id":       ac.id,
                "label":    ac.label or ac.config_type,
                "url":      h(ac.file_path) if ac.file_path else "",
                "type":     ac.config_type,
                "boot_arg": config_boot_arg(
                    ac.config_type,
                    os_type.slug,
                    h(ac.file_path) if ac.file_path else "",
                ),
            }
            for ac in v.autoconfigs
        ],
    }


def regenerate_all(db: Session) -> list[str]:
    """Regenerate every menu file. Returns list of written file paths."""
    cfg = Settings()  # Relecture .env à chaque génération (sans redémarrage uvicorn)
    cfg.menus_dir.mkdir(parents=True, exist_ok=True)
    env = _jinja_env()
    written: list[str] = []

    os_types = sort_os_types_for_ui(db.query(OsType).all())

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
                entry = _build_entry(v, os_type, cfg)
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
            base = cfg.server_base_url.rstrip("/")
            content = tmpl.render(
                os_type=os_type,
                entries=standard_entries,
                has_autres=has_autres,
                server_url=base,
                ubuntu_nfs_enabled=cfg.ubuntu_nfs_enabled,
                ubuntu_nfs_host=cfg.ubuntu_nfs_server_hostname() or "",
                ubuntu_nfs_export_path=(Path(cfg.http_root) / "boot" / "ubuntu").as_posix(),
            )
            out = cfg.menus_dir / f"{os_type.slug}.ipxe"
            out.write_text(content, encoding="utf-8")
            written.append(str(out))

            # ── Sous-menu "Autres" (scripts iPXE custom) ────────────────────
            if has_autres:
                tmpl_autres = env.get_template("autres.ipxe.j2")
                content_autres = tmpl_autres.render(
                    os_type=os_type,
                    entries=custom_entries,
                    server_url=base,
                )
                out_autres = cfg.menus_dir / f"{os_type.slug}_autres.ipxe"
                out_autres.write_text(content_autres, encoding="utf-8")
                written.append(str(out_autres))
                logger.info("Menu Autres généré : %s (%d entrées)", out_autres, len(custom_entries))
            else:
                # Supprimer l'ancien _autres.ipxe s'il n'y a plus de versions custom
                old = cfg.menus_dir / f"{os_type.slug}_autres.ipxe"
                old.unlink(missing_ok=True)

        except Exception:
            logger.exception("Erreur génération menu pour OS type '%s'", os_type.slug)

    # Central menu
    remote_chains = db.query(RemoteChain).filter(RemoteChain.enabled == True).order_by(RemoteChain.id).all()  # noqa: E712
    tmpl = env.get_template("menu.ipxe.j2")
    content = tmpl.render(
        os_types=os_types,
        server_url=cfg.server_base_url.rstrip("/"),
        remote_chains=remote_chains,
    )
    out = cfg.menus_dir / "menu.ipxe"
    out.write_text(content, encoding="utf-8")
    written.append(str(out))

    return written


def _has_ip_kernel_arg(s: str) -> bool:
    if not s or not s.strip():
        return False
    return bool(re.search(r"(?:^|\s)ip=", s))


def _build_kernel_args(
    be,
    os_slug: str,
    cfg: Settings,
    nfsroot_pair: str | None = None,
) -> str:
    """
    Concatène les args DB et ajoute modloop (Alpine). Pour Ubuntu NFS : ajoute après le
    téléchargement HTTP de vmlinuz/initrd par iPXE les paramètres noyau permettant au
    live (casper) de lire le squashfs sur NFS : ``ip=dhcp`` si besoin, ``boot=casper``,
    ``netboot=nfs``, ``nfsroot=<hôte>:<chemin>(,opts)``.
    """
    args = be.kernel_args if be and be.kernel_args else ""

    if os_slug == "alpine" and be and be.modloop_path:
        modloop_url = _http(be.modloop_path, cfg)
        if "modloop=" not in args:
            args = f"{args} modloop={modloop_url}".strip()

    # Ubuntu ISO extraite : indiquer explicitement NFS pour monter casper depuis la racine exportée.
    if os_slug.lower() != "ubuntu" or not nfsroot_pair or "nfsroot=" in args:
        return args

    nfs_bits = ["boot=casper", "netboot=nfs", f"nfsroot={nfsroot_pair}"]
    if not _has_ip_kernel_arg(args):
        nfs_bits.insert(0, "ip=dhcp")
    args = f"{args} {' '.join(nfs_bits)}".strip()
    return args


def _find_unattend_url(v: IsoVersion, os_type: OsType, cfg: Settings) -> str:
    """Cherche autounattend.xml à la racine du dossier boot de la version."""
    if os_type.boot_type != "windows":
        return ""
    be = v.boot_entry
    version_slug = _boot_os_version_segment(be, os_type.slug) or slugify(v.version_label)
    path = cfg.boot_dir / os_type.slug / version_slug / "autounattend.xml"
    if path.exists():
        return _http(f"boot/{os_type.slug}/{version_slug}/autounattend.xml", cfg)
    return ""


def _http(relative_path: str | None, cfg: Settings) -> str:
    if not relative_path:
        return ""
    clean = relative_path.replace("\\", "/").lstrip("/")
    base = cfg.server_base_url.rstrip("/")
    return f"{base}/{clean}"
