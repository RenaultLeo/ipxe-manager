"""
Generates all .ipxe menu files from the database and Jinja2 templates.
"""
import json
import logging
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, resolve_server_base_url
from app.models.models import OsType, IsoVersion, BootEntry, RemoteChain
from app.services.config_scanner import config_boot_arg
from app.services.os_type_order import sort_os_types_for_ui
from app.services.slugify import slugify
from app.services.autoconfig_label import resolve_autoconfig_menu_label
from app.services.autoconfig_publish import published_seed_dir_rel_path
from app.services.filesystem_perms import prepare_menus_dir, write_text_file

logger = logging.getLogger(__name__)


def _write_menu(path: Path, content: str) -> None:
    write_text_file(path, content, file_mode=0o664)

TMPL_DIR = Path(__file__).parent.parent / "ipxe_templates"

# Rocky, AlmaLinux, CentOS : inst.repo=  |  Fedora : inst.stage2= + rd.neednet=1 (Live / ISO extraite)
_EL_ANACONDA_FULL_ISO_SLUGS = frozenset({"rocky", "alma", "centos", "fedora"})
# Debian netinst : inst.repo= vers la racine HTTP de l'ISO extraite (dists/ intact via xorriso)
_DEBIAN_NETINST_SLUGS = frozenset({"debian"})

# Dépôt APK public par défaut (installateur netboot Alpine)
ALPINE_REPO_DEFAULT_PUBLIC = "http://dl-cdn.alpinelinux.org/alpine/latest-stable/main"

MENU_LOGO_UPLOAD_NAME = "menu-logo-upload.png"

# VMware / OEM : parfois ``prefix-http=`` ou espaces ; sans ça la mise à jour pouvait tout ignorer (bug silencieux).
_ESXI_IPXE_PREFIX_LINE_RE = re.compile(r"^\s*prefix(?:-http)?\s*=", re.I)
_ESXI_IPXE_KERNELOPT_LINE_RE = re.compile(r"^\s*kernelopt\s*=", re.I)

# Fichier embarqué (app/resources/default_menu_logo.png) si pas d’upload utilisateur.
DEFAULT_MENU_LOGO = Path(__file__).resolve().parent.parent / "resources" / "default_menu_logo.png"


def _esxi_kernel_basename_from_boot_cfg(boot_cfg: Path) -> str | None:
    """Lit ``kernel=<basename>`` dans boot.cfg aplati à côté de mboot (insensible ligne #)."""
    if not boot_cfg.is_file():
        return None
    try:
        text = boot_cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("kernel="):
            val = line.split("=", 1)[1].strip().strip("\"'")
            return Path(val.replace("\\", "/")).name
    return None


def _esxi_kernelopt_merge(existing: str, boot_arg: str) -> str:
    """Fusionne les options kernelopt ESXi (en forçant runweasel + ks=...)."""
    tokens: list[str] = []
    for tok in (existing or "").split():
        t = tok.strip()
        if not t:
            continue
        if t.lower() == "cdromboot":
            continue
        tokens.append(t)
    if not any(t.lower() == "runweasel" for t in tokens):
        tokens.insert(0, "runweasel")
    for tok in (boot_arg or "").split():
        t = tok.strip()
        if not t:
            continue
        if t not in tokens:
            tokens.append(t)
    return " ".join(tokens).strip()


def _esxi_boot_cfg_with_boot_arg(text: str, boot_arg: str) -> str:
    """Injecte ``ks=...`` dans kernelopt d'un ``ipxe-boot.cfg`` ESXi."""
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if _ESXI_IPXE_KERNELOPT_LINE_RE.match(line):
            cur = line.split("=", 1)[1].strip() if "=" in line else ""
            merged = _esxi_kernelopt_merge(cur, boot_arg)
            out.append(f"kernelopt={merged}" if merged else "kernelopt=")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        merged = _esxi_kernelopt_merge("", boot_arg)
        insert_at = 0
        for i, raw in enumerate(out):
            s = raw.strip().lower()
            if s.startswith("#") or not s:
                continue
            insert_at = i + 1 if s.startswith("kernel=") else i
            break
        out.insert(insert_at, f"kernelopt={merged}" if merged else "kernelopt=")
    return "\n".join(out).rstrip() + "\n"


def _materialize_esxi_autoconfig_boot_cfgs(
    cfg: Settings,
    base_cfg_rel: str,
    autoconfigs: list[dict],
    *,
    file_suffix: str = "",
) -> list[dict]:
    """Crée un boot.cfg dérivé par config ESXi et renvoie les URLs HTTP correspondantes."""
    rel = (base_cfg_rel or "").strip().lstrip("/")
    if not rel:
        return autoconfigs
    src = cfg.http_root and (Path(cfg.http_root) / rel)
    if not src or not src.is_file():
        return autoconfigs
    try:
        base_text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return autoconfigs

    out: list[dict] = []
    version_dir = src.parent
    for ac in autoconfigs:
        boot_arg = (ac.get("boot_arg") or "").strip()
        if not boot_arg:
            out.append(ac)
            continue
        ac_id = ac.get("id")
        if not ac_id:
            out.append(ac)
            continue
        dst = version_dir / f"ipxe-boot-ac{int(ac_id)}{file_suffix}.cfg"
        try:
            write_text_file(dst, _esxi_boot_cfg_with_boot_arg(base_text, boot_arg), file_mode=0o644)
            rel_cfg = dst.relative_to(Path(cfg.http_root)).as_posix()
            out.append({**ac, "esxi_boot_cfg": _http(rel_cfg, cfg)})
        except OSError:
            out.append(ac)
    return out


def _jinja_env() -> Environment:
    from app.config import settings as app_settings

    env = Environment(
        loader=FileSystemLoader(str(TMPL_DIR)),
        keep_trailing_newline=True,
    )
    env.globals["ipxe_debug"] = app_settings.ipxe_debug
    return env


def _boot_os_version_segment(be: BootEntry | None, os_slug: str) -> str | None:
    """
    Nom du dossier version sous http/boot/<os_slug>/, tel qu'enregistré après extraction
    (kernel_path / initrd_path). Aligne nfsroot= et la structure disque
    ``http/boot/<os>/<version>`` (ex. ubuntu24.04) sans dépendre du libellé affiché.
    """
    if not be:
        return None
    for rel in (
        be.kernel_path,
        be.initrd_path,
        getattr(be, "esxi_boot_cfg_path", None),
        getattr(be, "esxi_efi_boot_path", None),
    ):
        if not rel:
            continue
        parts = rel.replace("\\", "/").lstrip("/").split("/")
        if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == os_slug.lower():
            return parts[2]
    return None


def _esxi_module_urls_from_json(
    raw_json: str | None,
    vr: str,
    h,
    *,
    version_label: str = "",
    field_name: str = "esxi_modules",
) -> list[str]:
    """Construit les URLs HTTP des ``module`` iPXE depuis un JSON liste de chemins relatifs."""
    urls: list[str] = []
    if not raw_json or not raw_json.strip():
        return urls
    try:
        parsed_mods = json.loads(raw_json)
        if not isinstance(parsed_mods, list):
            logger.warning(
                'ESXi %s : JSON attendu liste pour la version "%s".',
                field_name,
                version_label or "?",
            )
            return urls
        mod_names = [x for x in parsed_mods if isinstance(x, str)]
    except json.JSONDecodeError:
        logger.warning(
            'ESXi %s JSON invalide pour la version "%s".',
            field_name,
            version_label or "?",
        )
        return urls
    for m in mod_names:
        mnorm = m.replace("\\", "/").lstrip("/")
        urls.append(h(f"{vr}/{mnorm}"))
    return urls


def _ubuntu_nocloud_boot_arg(seed_dir_url: str) -> str:
    """Args autoinstall : seed à la racine boot/ubuntu/<release>/ (ds=nocloud;s=…/)."""
    base = (seed_dir_url or "").rstrip("/") + "/"
    if base == "/":
        return ""
    return f"autoinstall ds=nocloud;s={base} cloud-config-url=/dev/null"


def _ubuntu_server_bundle_rel_path(v: IsoVersion, cloud_slug: str) -> str:
    """Dossier HTTP cloud-init Server : boot/ubuntu/<release>/conf-cloudInit-<slug>/."""
    be = v.boot_entry
    seg = _boot_os_version_segment(be, "ubuntu") or slugify(v.version_label)
    return f"boot/ubuntu/{seg}/conf-cloudInit-{cloud_slug}"


def _ubuntu_server_seed_dir_rel(v: IsoVersion, ac) -> str | None:
    slug = (getattr(ac, "ubuntu_cloud_slug", None) or "").strip()
    if slug:
        return _ubuntu_server_bundle_rel_path(v, slug)
    rel = (ac.file_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    parts = rel.split("/")
    for i, part in enumerate(parts):
        if part.startswith("conf-cloudInit"):
            return "/".join(parts[: i + 1])
    return None


def _ubuntu_server_flat_items(entries: list[dict]) -> list[dict]:
    """Menu Server plat : une ligne par config (+ manuel par version)."""
    flat: list[dict] = []
    for e in entries:
        k = e.get("kernel") or ""
        i = e.get("initrd") or ""
        ka = e.get("kernel_args") or ""
        vid = e["id"]
        base = e.get("label") or f"Ubuntu {vid}"
        for ac in e.get("autoconfigs") or []:
            flat.append(
                {
                    "menu_id": f"ac{vid}_{ac['id']}",
                    "label": f"{base} — {ac['label']}",
                    "kernel_url": k,
                    "initrd_url": i,
                    "kernel_args": ka,
                    "boot_arg": ac.get("boot_arg") or "",
                }
            )
        flat.append(
            {
                "menu_id": f"manual_{vid}",
                "label": f"{base} — installation manuelle",
                "kernel_url": k,
                "initrd_url": i,
                "kernel_args": ka,
                "boot_arg": "",
            }
        )
    return flat


def _menu_autoconfig_entries(
    v: IsoVersion,
    os_type: OsType,
    h,
    *,
    ubuntu_variant: str = "desktop",
) -> list[dict]:
    """Entrées autoconfig pour les sous-menus iPXE."""
    slug_l = os_type.slug.lower()
    variant = (ubuntu_variant or "desktop").lower()
    configs = list(v.autoconfigs or [])
    entries: list[dict] = []

    if slug_l == "ubuntu" and variant == "desktop":
        active_id = getattr(v, "active_autoconfig_id", None)
        if active_id:
            configs = [ac for ac in configs if ac.id == active_id]
        for ac in configs:
            rel = ac.file_path or ""
            boot_arg = config_boot_arg(ac.config_type, os_type.slug, h(rel) if rel else "")
            if active_id == ac.id and ac.ubuntu_cloud_slug:
                rel = published_seed_dir_rel_path(v)
                boot_arg = _ubuntu_nocloud_boot_arg(h(rel))
            url = h(rel) if rel else ""
            entries.append(
                {
                    "id": ac.id,
                    "label": resolve_autoconfig_menu_label(ac),
                    "url": url,
                    "type": ac.config_type,
                    "boot_arg": boot_arg,
                }
            )
        return entries

    if slug_l == "ubuntu" and variant == "server":
        for ac in configs:
            dir_rel = _ubuntu_server_seed_dir_rel(v, ac)
            if not dir_rel:
                continue
            boot_arg = _ubuntu_nocloud_boot_arg(h(dir_rel))
            entries.append(
                {
                    "id": ac.id,
                    "label": resolve_autoconfig_menu_label(ac),
                    "url": h(dir_rel),
                    "type": ac.config_type,
                    "boot_arg": boot_arg,
                }
            )
        return entries

    if slug_l == "proxmox":
        active_id = getattr(v, "active_autoconfig_id", None)
        pve_configs = [ac for ac in configs if ac.config_type == "proxmox-answer"]
        if active_id:
            pve_configs = [ac for ac in pve_configs if ac.id == active_id]
        else:
            pve_configs = []
        for ac in pve_configs:
            rel = ac.file_path or ""
            boot_arg = config_boot_arg(ac.config_type, os_type.slug, h(rel) if rel else "")
            entries.append(
                {
                    "id": ac.id,
                    "label": resolve_autoconfig_menu_label(ac),
                    "url": h(rel) if rel else "",
                    "type": ac.config_type,
                    "boot_arg": boot_arg,
                }
            )
        return entries

    if slug_l == "esxi":
        active_id = getattr(v, "active_autoconfig_id", None)
        if not active_id:
            return []
        for ac in configs:
            if ac.id != active_id:
                continue
            rel = ac.file_path or ""
            boot_arg = config_boot_arg(ac.config_type, os_type.slug, h(rel) if rel else "")
            entries.append(
                {
                    "id": ac.id,
                    "label": resolve_autoconfig_menu_label(ac),
                    "url": h(rel) if rel else "",
                    "type": ac.config_type,
                    "boot_arg": boot_arg,
                }
            )
        return entries

    for ac in configs:
        rel = ac.file_path or ""
        boot_arg = config_boot_arg(ac.config_type, os_type.slug, h(rel) if rel else "")
        url = h(rel) if rel else ""
        entries.append(
            {
                "id": ac.id,
                "label": resolve_autoconfig_menu_label(ac),
                "url": url,
                "type": ac.config_type,
                "boot_arg": boot_arg,
            }
        )
    return entries


def _build_entry(v: IsoVersion, os_type: OsType, cfg: Settings) -> dict:
    """Construit le dict d'une version pour les templates Jinja2."""
    be = v.boot_entry
    version_slug = _boot_os_version_segment(be, os_type.slug) or slugify(v.version_label)
    use_ubuntu_nfs = (
        os_type.slug.lower() == "ubuntu"
        and bool(getattr(v, "ubuntu_nfs_boot", False))
    )
    nfs_pair = (
        cfg.ubuntu_nfsroot_pair(os_type.slug, version_slug) if use_ubuntu_nfs else None
    )
    if use_ubuntu_nfs and not nfs_pair:
        logger.warning(
            "Ubuntu NFS: boot NFS activé pour \"%s\" mais nfsroot vide. "
            "Vérifier HTTP_ROOT, boot/ubuntu/<slug> et UBUNTU_NFS_HOST, puis régénérer les menus.",
            v.version_label,
        )
    elif use_ubuntu_nfs and nfs_pair:
        nfs_dir = cfg.ubuntu_boot_version_dir(version_slug)
        if not nfs_dir.is_dir():
            logger.warning(
                "Ubuntu NFS: répertoire absent sur ce serveur : %s — le client affichera souvent "
                "« No such file or directory » au montage. Vérifier le nom du dossier (slug) sous "
                "boot/ubuntu/, qu’il correspond aux chemins kernel/initrd, puis exportfs -ra.",
                nfs_dir,
            )

    def h(rel: str | None) -> str:
        return _http(rel, cfg)

    def h_win(rel: str | None) -> str:
        if not rel:
            return ""
        from app.services.windows_boot_paths import canonicalize_rel

        return _http(canonicalize_rel(rel), cfg)

    esxi_boot_http = ""
    esxi_module_urls: list[str] = []
    esxi_module_urls_legacy: list[str] = []
    esxi_boot_http_legacy = ""
    esxi_vr = ""
    # iPXE : le 1er token de imgargs = basename de l’URL « kernel » (même casse que les fichiers ISO extraits).
    esxi_mboot_basename = ""
    slug_l = os_type.slug.lower()
    bt_l = (os_type.boot_type or "linux").lower()
    if be and (slug_l == "esxi" or bt_l == "esxi"):
        esxi_boot_http = h(getattr(be, "esxi_boot_cfg_path", None))
        esxi_boot_http_legacy = h(
            getattr(be, "esxi_boot_cfg_legacy_path", None)
            or getattr(be, "esxi_boot_cfg_path", None)
        )
        raw_mods = getattr(be, "esxi_modules", "") or ""
        seg = _boot_os_version_segment(be, os_type.slug) or slugify(v.version_label)
        esxi_vr = f"boot/{os_type.slug}/{seg}".replace("\\", "/")
        esxi_module_urls = _esxi_module_urls_from_json(
            raw_mods,
            esxi_vr,
            h,
            version_label=v.version_label,
            field_name="esxi_modules",
        )
        esxi_module_urls_legacy = _esxi_module_urls_from_json(
            getattr(be, "esxi_modules_legacy", "") or "",
            esxi_vr,
            h,
            version_label=v.version_label,
            field_name="esxi_modules_legacy",
        )
        if be.kernel_path:
            esxi_mboot_basename = (
                be.kernel_path.replace("\\", "/").rstrip("/").split("/")[-1]
            )
    ubuntu_variant = ""
    if slug_l == "ubuntu":
        ubuntu_variant = (getattr(v, "ubuntu_variant", None) or "desktop").lower()
        if ubuntu_variant not in ("desktop", "server"):
            ubuntu_variant = "desktop"
    autoconfigs = _menu_autoconfig_entries(
        v, os_type, h, ubuntu_variant=ubuntu_variant or "desktop"
    )

    winpe_active_label = ""
    mode_suffix = ""
    if be and (os_type.boot_type or "").lower() == "windows":
        wmode = (getattr(v, "windows_mode", None) or "desktop").lower()
        pmode = (getattr(v, "winpe_mode", None) or "master").lower()
        if wmode == "winpe":
            mode_suffix = " [WinPE Util]" if pmode == "utility" else " [WinPE Master]"
        else:
            mode_suffix = " [Desktop]"
        active_wid = getattr(v, "active_winpe_install_id", None)
        if active_wid:
            awi = next(
                (w for w in (v.winpe_installs or []) if w.id == active_wid),
                None,
            )
            if awi:
                winpe_active_label = (awi.label or awi.slug or "").strip()

    return {
        "id":           v.id,
        "label":        f"{os_type.label} {v.version_label}{mode_suffix}",
        "winpe_active_label": winpe_active_label,
        "kernel":       h(be.kernel_path) if be and be.kernel_path else "",
        "initrd":       h(be.initrd_path) if be and be.initrd_path else "",
        "boot_wim":     h_win(be.boot_wim_path) if be and be.boot_wim_path else "",
        "bcd":          h_win(be.bcd_path) if be and be.bcd_path else "",
        "boot_sdi":     h_win(be.boot_sdi_path) if be and be.boot_sdi_path else "",
        "unattend_url": _find_unattend_url(v, os_type, cfg),
        "bootmgr":      h_win(be.bootmgr_path) if be and be.bootmgr_path else "",
        "custom_ipxe":  h(be.custom_ipxe_path) if be and be.custom_ipxe_path else "",
        "modloop":      h(be.modloop_path) if be and be.modloop_path else "",
        "esxi_boot_cfg": esxi_boot_http,
        "esxi_boot_cfg_legacy": esxi_boot_http_legacy,
        "esxi_mboot_basename": esxi_mboot_basename,
        "esxi_module_urls": esxi_module_urls,
        "esxi_module_urls_legacy": esxi_module_urls_legacy,
        # Pour Alpine / Ubuntu ; pour ESXi : options pass-through vers imgargs mboot.c32
        "kernel_args": _build_kernel_args(
            be, os_type.slug, cfg, nfsroot_pair=nfs_pair, iso_version=v
        ),
        "boot_type":   os_type.boot_type or "linux",
        "ubuntu_variant": ubuntu_variant,
        "autoconfigs": autoconfigs,
        "ipxe_item_tag": f"v{v.id}",
        "ubuntu_nfs_boot": use_ubuntu_nfs,
        "ubuntu_direct_boot": (
            slug_l == "ubuntu"
            and ubuntu_variant == "desktop"
            and bool(getattr(v, "active_autoconfig_id", None))
            and not use_ubuntu_nfs
        ),
        **(
            _proxmox_menu_fields(v, be, cfg)
            if slug_l == "proxmox"
            else {}
        ),
    }



def _build_menu_theme_png(menus_dir: Path) -> bool:
    """
    Génère menus/menu-theme.png : fond bleu ardoise ; logo en bas à droite depuis
    menus/menu-logo-upload.png (Paramètres) si présent, sinon bannière incluse
    app/resources/default_menu_logo.png. Nécessite Pillow.
    """
    out_path = menus_dir / "menu-theme.png"
    upload_logo = menus_dir / MENU_LOGO_UPLOAD_NAME
    if upload_logo.is_file():
        logo_src: Path | None = upload_logo
    elif DEFAULT_MENU_LOGO.is_file():
        logo_src = DEFAULT_MENU_LOGO
    else:
        logo_src = None
    for stale in (menus_dir / "menu-brand.png", menus_dir / "menu-background.png"):
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass

    try:
        from PIL import Image
    except ImportError:
        if out_path.is_file():
            try:
                out_path.unlink()
            except OSError:
                pass
        logger.warning("Pillow absent : menu-theme.png non généré (pip install pillow).")
        return False

    w, h = 1280, 720
    bg_color = (22, 42, 74)
    canvas = Image.new("RGB", (w, h), bg_color)

    if logo_src is not None and logo_src.is_file():
        try:
            logo = Image.open(logo_src).convert("RGBA")
            max_w = min(200, w // 4)
            if logo.width > max_w:
                ratio = max_w / logo.width
                new_h = max(1, int(logo.height * ratio))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                logo = logo.resize((max_w, new_h), resample)
            pad = 28
            x = w - logo.width - pad
            y = h - logo.height - pad
            canvas.paste(logo, (x, y), logo)
        except OSError as e:
            logger.warning("Logo menu illisible (%s) — fond bleu seul.", e)

    prepare_menus_dir(menus_dir)
    try:
        canvas.save(out_path, "PNG", optimize=True)
        try:
            out_path.chmod(0o664)
        except OSError:
            pass
    except OSError as e:
        logger.error("Écriture %s : %s", out_path, e)
        return False

    logger.info("menu-theme.png généré : %s", out_path)
    return True


def _refresh_esxi_ipxe_boot_cfg_prefixes(cfg: Settings) -> None:
    """
    Réécrit ``prefix=`` / ``prefix-http=`` → ``prefix=`` dans chaque ``ipxe-boot.cfg`` ESXi,
    normalise ``kernel=`` / ``modules=`` / ``module=`` en minuscules, reflète ``server_base_url``.
    Import local évite tout cycle au chargement du module menus.
    """
    from app.services.iso_extractor import normalize_esxi_ipxe_boot_cfg_paths

    esxi_root = cfg.boot_dir / "esxi"
    if not esxi_root.is_dir():
        return
    base = cfg.server_base_url.rstrip("/")
    for d in sorted(esxi_root.iterdir()):
        if not d.is_dir():
            continue
        ver = d.name
        new_line = f"prefix={base}/boot/esxi/{ver}/"
        candidates = (d / "ipxe-boot.cfg", d / "ipxe-boot-legacy.cfg")
        for path in candidates:
            if not path.is_file():
                continue
            fname = path.relative_to(d).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning("ESXi %s illisible %s : %s", fname, path, e)
                continue
            if path.name == "ipxe-boot.cfg":
                text = normalize_esxi_ipxe_boot_cfg_paths(text)
            lines = text.splitlines()
            out: list[str] = []
            replaced_prefix = False
            for line in lines:
                if _ESXI_IPXE_PREFIX_LINE_RE.match(line):
                    out.append(new_line)
                    replaced_prefix = True
                else:
                    out.append(line)
            if not replaced_prefix:
                insert_at = 0
                while insert_at < len(out) and out[insert_at].strip().startswith("#"):
                    insert_at += 1
                out.insert(insert_at, new_line)
            try:
                write_text_file(path, "\n".join(out) + "\n", file_mode=0o644)
            except OSError as e:
                logger.warning("ESXi %s non écrit %s : %s", fname, path, e)


def queue_regenerate_all() -> None:
    """Régénère les menus en arrière-plan (Celery, sinon thread daemon)."""
    try:
        from app.tasks.jobs import regenerate_menus_task

        regenerate_menus_task.delay()
        return
    except Exception:
        logger.debug("Celery indisponible — régénération menus en thread")

    import threading

    from app.database import SessionLocal

    def _run() -> None:
        db = SessionLocal()
        try:
            regenerate_all(db)
        except Exception:
            logger.exception("regenerate_all (arrière-plan)")
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()


def regenerate_all(db: Session) -> list[str]:
    """Regenerate every menu file. Returns list of written file paths."""
    cfg = Settings()  # Relecture .env à chaque génération (sans redémarrage uvicorn)
    cfg.server_base_url = resolve_server_base_url(db)
    _refresh_esxi_ipxe_boot_cfg_prefixes(cfg)
    prepare_menus_dir(cfg.menus_dir)
    repo_root = Path(__file__).resolve().parent.parent.parent
    has_menu_theme = _build_menu_theme_png(cfg.menus_dir)

    env = _jinja_env()
    written: list[str] = []

    all_os_types = sort_os_types_for_ui(db.query(OsType).all())
    winpe_os_type = next(
        (ot for ot in all_os_types if (ot.slug or "").lower() == "winpe"),
        None,
    )
    os_types = [ot for ot in all_os_types if (ot.slug or "").lower() != "winpe"]

    # Per-OS sub-menus
    for os_type in os_types:
        try:
            versions = (
                db.query(IsoVersion)
                .options(
                    joinedload(IsoVersion.boot_entry),
                    joinedload(IsoVersion.autoconfigs),
                    joinedload(IsoVersion.winpe_installs),
                )
                .filter(
                    IsoVersion.os_type_id == os_type.id,
                    IsoVersion.status == "ready",
                )
                .all()
            )
            version_with_os: list[tuple[IsoVersion, OsType]] = [
                (v, os_type) for v in versions
            ]
            if (os_type.slug or "").lower() == "windows" and winpe_os_type is not None:
                legacy_winpe_versions = (
                    db.query(IsoVersion)
                    .options(
                        joinedload(IsoVersion.boot_entry),
                        joinedload(IsoVersion.autoconfigs),
                        joinedload(IsoVersion.winpe_installs),
                    )
                    .filter(
                        IsoVersion.os_type_id == winpe_os_type.id,
                        IsoVersion.status == "ready",
                    )
                    .all()
                )
                version_with_os.extend((v, winpe_os_type) for v in legacy_winpe_versions)

            # Séparer : versions standard vs versions avec script iPXE custom
            standard_entries = []
            custom_entries   = []
            for v, entry_os_type in version_with_os:
                entry = _build_entry(v, entry_os_type, cfg)
                if entry["custom_ipxe"]:
                    custom_entries.append(entry)
                    continue

                slug_l = (entry_os_type.slug or "").lower()
                bt_l = (entry_os_type.boot_type or "linux").lower()
                is_esxi = slug_l == "esxi" or bt_l == "esxi"

                if is_esxi and v.boot_entry:
                    be = v.boot_entry
                    efi_rel = (getattr(be, "esxi_efi_boot_path", None) or "").strip()
                    has_leg = bool(be.kernel_path and str(be.kernel_path).strip())
                    rows: list[dict] = []
                    cfg_http = _http(getattr(be, "esxi_boot_cfg_path", None), cfg)
                    if efi_rel:
                        kb = efi_rel.replace("\\", "/").rstrip("/").split("/")[-1]
                        rows.append(
                            {
                                **entry,
                                "label": f"{entry['label']} [UEFI]",
                                "kernel": _http(efi_rel, cfg),
                                "esxi_boot_cfg": cfg_http,
                                "esxi_mboot_basename": kb,
                                "esxi_module_urls": entry["esxi_module_urls"],
                                "ipxe_item_tag": f"v{v.id}_uefi",
                            }
                        )
                    if has_leg:
                        kp = be.kernel_path or ""
                        kb = kp.replace("\\", "/").rstrip("/").split("/")[-1]
                        leg_suffix = " [Legacy]" if efi_rel else ""
                        legacy_cfg_http = _http(
                            getattr(be, "esxi_boot_cfg_legacy_path", None)
                            or getattr(be, "esxi_boot_cfg_path", None),
                            cfg,
                        )
                        rows.append(
                            {
                                **entry,
                                "label": f"{entry['label']}{leg_suffix}",
                                "kernel": _http(kp, cfg),
                                "esxi_boot_cfg": legacy_cfg_http,
                                "esxi_mboot_basename": kb,
                                "esxi_module_urls": (
                                    entry.get("esxi_module_urls_legacy")
                                    or entry["esxi_module_urls"]
                                ),
                                "ipxe_item_tag": f"v{v.id}_leg" if efi_rel else f"v{v.id}",
                            }
                        )
                    if rows:
                        standard_entries.extend(rows)
                        continue

                standard_entries.append(entry)

            has_autres = len(custom_entries) > 0
            base = cfg.server_base_url.rstrip("/")
            custom_os = not bool(getattr(os_type, "is_builtin", True))

            # ── Menu principal de l'OS ──────────────────────────────────────
            if custom_os:
                tmpl_bridge = env.get_template("custom_os_bridge.ipxe.j2")
                content = tmpl_bridge.render(
                    os_type=os_type,
                    has_autres=has_autres,
                    server_url=base,
                    has_menu_theme=has_menu_theme,
                )
                out = cfg.menus_dir / f"{os_type.slug}.ipxe"
                _write_menu(out, content)
                written.append(str(out))

                autres_back_target = f"{base}/menus/menu.ipxe"
                autres_back_item = "Retour au menu principal"
            else:
                slug_l = (os_type.slug or "").lower()
                bt_l = (os_type.boot_type or "linux").lower()

                if slug_l == "ubuntu":
                    desktop_entries = [
                        e
                        for e in standard_entries
                        if e.get("ubuntu_variant") != "server"
                    ]
                    server_entries = [
                        e
                        for e in standard_entries
                        if e.get("ubuntu_variant") == "server"
                    ]
                    hub = env.get_template("ubuntu_hub.ipxe.j2")
                    out_hub = cfg.menus_dir / "ubuntu.ipxe"
                    _write_menu(
                        out_hub,
                        hub.render(server_url=base, has_menu_theme=has_menu_theme),
                    )
                    written.append(str(out_hub))

                    linux_tmpl = env.get_template("linux.ipxe.j2")
                    out_desktop = cfg.menus_dir / "ubuntu_desktop.ipxe"
                    _write_menu(
                        out_desktop,
                        linux_tmpl.render(
                            os_type=os_type,
                            entries=desktop_entries,
                            has_autres=has_autres,
                            server_url=base,
                            has_menu_theme=has_menu_theme,
                            back_menu_url=f"{base}/menus/ubuntu.ipxe",
                            back_item_label="Retour au menu Ubuntu",
                            ubuntu_nfs_enabled=any(
                                e.get("ubuntu_nfs_boot") for e in desktop_entries
                            ),
                            ubuntu_nfs_host=cfg.ubuntu_nfs_server_hostname() or "",
                            ubuntu_nfs_export_path=(
                                Path(cfg.http_root) / "boot" / "ubuntu"
                            ).as_posix(),
                        ),
                    )
                    written.append(str(out_desktop))

                    flat = _ubuntu_server_flat_items(server_entries)
                    server_tmpl = env.get_template("ubuntu_server.ipxe.j2")
                    out_server = cfg.menus_dir / "ubuntu_server.ipxe"
                    _write_menu(
                        out_server,
                        server_tmpl.render(
                            flat_items=flat,
                            default_id=flat[0]["menu_id"] if flat else "back",
                            has_autres=has_autres,
                            server_url=base,
                            has_menu_theme=has_menu_theme,
                        ),
                    )
                    written.append(str(out_server))

                    autres_back_target = f"{base}/menus/ubuntu.ipxe"
                    autres_back_item = "Retour au menu Ubuntu"
                else:
                    if slug_l == "esxi" or bt_l == "esxi":
                        tmpl_name = "esxi.ipxe.j2"
                    elif bt_l == "windows":
                        tmpl_name = "windows.ipxe.j2"
                    elif slug_l == "proxmox":
                        tmpl_name = "proxmox.ipxe.j2"
                    else:
                        tmpl_name = "linux.ipxe.j2"
                    if not (TMPL_DIR / tmpl_name).exists():
                        tmpl_name = "linux.ipxe.j2"

                    tmpl = env.get_template(tmpl_name)
                    content = tmpl.render(
                        os_type=os_type,
                        entries=standard_entries,
                        has_autres=has_autres,
                        server_url=base,
                        has_menu_theme=has_menu_theme,
                        ubuntu_nfs_enabled=any(
                            e.get("ubuntu_nfs_boot") for e in standard_entries
                        ),
                        ubuntu_nfs_host=cfg.ubuntu_nfs_server_hostname() or "",
                        ubuntu_nfs_export_path=(
                            Path(cfg.http_root) / "boot" / "ubuntu"
                        ).as_posix(),
                    )
                    out = cfg.menus_dir / f"{os_type.slug}.ipxe"
                    _write_menu(out, content)
                    written.append(str(out))

                    autres_back_target = f"{base}/menus/{os_type.slug}.ipxe"
                    autres_back_item = f"Retour à {os_type.label}"

            # ── Sous-menu "Autres" (scripts iPXE custom) ────────────────────
            if has_autres:
                tmpl_autres = env.get_template("autres.ipxe.j2")
                content_autres = tmpl_autres.render(
                    os_type=os_type,
                    entries=custom_entries,
                    server_url=base,
                    has_menu_theme=has_menu_theme,
                    back_menu_url=autres_back_target,
                    back_item_label=autres_back_item,
                )
                out_autres = cfg.menus_dir / f"{os_type.slug}_autres.ipxe"
                _write_menu(out_autres, content_autres)
                written.append(str(out_autres))
                logger.info("Menu scripts iPXE généré : %s (%d entrées)", out_autres, len(custom_entries))
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
        has_menu_theme=has_menu_theme,
    )
    out = cfg.menus_dir / "menu.ipxe"
    _write_menu(out, content)
    written.append(str(out))

    # Ancien OS WinPE masqué du menu central : nettoyer les menus potentiellement restés sur disque.
    for stale_name in ("winpe.ipxe", "winpe_autres.ipxe"):
        stale = cfg.menus_dir / stale_name
        if stale.is_file():
            stale.unlink(missing_ok=True)

    return written


def _has_ip_kernel_arg(s: str) -> bool:
    if not s or not s.strip():
        return False
    return bool(re.search(r"(?:^|\s)ip=", s))


# Retirés avant d’appliquer l’autre mode (évite NFS + HTTP mélangés si .env ou kernel_args changent).
_UBUNTU_NFS_KERNEL_TOKENS = (
    r"\bboot=casper\b",
    r"\bnetboot=nfs\b",
    r"\bnfsroot=\S+",
    r"\bnfsopts=\S+",
)
_UBUNTU_HTTP_KERNEL_TOKENS = (
    r"\broot=/dev/ram0\b",
    r"\bramdisk_size=\S+",
    r"\burl=\S+",
)


def _strip_kernel_arg_tokens(args: str, patterns: tuple[str, ...]) -> str:
    out = args or ""
    for pat in patterns:
        out = re.sub(pat, "", out)
    return re.sub(r"\s+", " ", out).strip()


def _iso_http_url(iso_version: IsoVersion | None, cfg: Settings) -> str:
    """URL HTTP d’une ISO si ``iso_path`` est sous ``iso_root`` (Ubuntu, Proxmox, …)."""
    if iso_version is None:
        return ""
    raw = (iso_version.iso_path or "").strip()
    if not raw:
        return ""
    return cfg.iso_public_http_url(raw) or ""


def _proxmox_iso_http_url_for_menu(
    iso_version: IsoVersion | None,
    be: BootEntry | None,
    cfg: Settings,
    *,
    autoinstall: bool = False,
) -> str:
    """
    URL HTTP du 2e initrd ``proxmox.iso`` :
    - manuel : ``proxmox-netboot.iso``
    - auto-install : ``proxmox-netboot-autoinstall.iso`` (sinon repli sur manuel)
    """
    from app.services.iso_extractor import (
        PROXMOX_NETBOOT_AUTOINSTALL_BASENAME,
        PROXMOX_NETBOOT_DIRNAME,
        PROXMOX_NETBOOT_ISO_BASENAME,
        migrate_legacy_proxmox_netboot_isos,
    )

    if be:
        seg = _boot_os_version_segment(be, "proxmox")
        if seg:
            extract_dest = cfg.boot_dir / "proxmox" / seg
            netboot_sub = migrate_legacy_proxmox_netboot_isos(extract_dest)
            rel_prefix = f"boot/proxmox/{seg}/{PROXMOX_NETBOOT_DIRNAME}"
            if autoinstall:
                ais = netboot_sub / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME
                if ais.is_file():
                    return _http(
                        f"{rel_prefix}/{PROXMOX_NETBOOT_AUTOINSTALL_BASENAME}",
                        cfg,
                    )
                legacy_ais = extract_dest / PROXMOX_NETBOOT_AUTOINSTALL_BASENAME
                if legacy_ais.is_file():
                    return _http(
                        f"boot/proxmox/{seg}/{PROXMOX_NETBOOT_AUTOINSTALL_BASENAME}",
                        cfg,
                    )
            manual = netboot_sub / PROXMOX_NETBOOT_ISO_BASENAME
            if manual.is_file():
                return _http(f"{rel_prefix}/{PROXMOX_NETBOOT_ISO_BASENAME}", cfg)
            legacy_manual = extract_dest / PROXMOX_NETBOOT_ISO_BASENAME
            if legacy_manual.is_file():
                return _http(
                    f"boot/proxmox/{seg}/{PROXMOX_NETBOOT_ISO_BASENAME}", cfg
                )

    if autoinstall:
        return ""

    url = _iso_http_url(iso_version, cfg)
    if url:
        return url

    if iso_version is not None:
        pack = Path(cfg.iso_root) / "proxmox" / str(iso_version.id)
        if pack.is_dir():
            for p in sorted(pack.glob("*.iso")):
                if p.is_file():
                    return cfg.iso_public_http_url(p) or ""
    return ""


def _resolve_proxmox_boot(
    iso_version: IsoVersion | None,
    be: BootEntry | None,
    cfg: Settings,
) -> dict[str, str]:
    """
    Stratégie Proxmox : ``dual_initrd`` (initrd.img gzip + ISO en proxmox.iso) ou ``single`` si aucune ISO HTTP.
    Le paramètre noyau ``url=`` vers boot/ ne fonctionne pas — ne plus l’utiliser.
    """
    mode_pref = (getattr(cfg, "proxmox_boot_delivery", None) or "auto").strip().lower()
    manual_url = _proxmox_iso_http_url_for_menu(
        iso_version, be, cfg, autoinstall=False
    )
    autoinstall_url = _proxmox_iso_http_url_for_menu(
        iso_version, be, cfg, autoinstall=True
    )

    if mode_pref == "single":
        mode = "single"
    elif manual_url:
        mode = "dual_initrd"
    else:
        mode = "single"

    if mode == "single" and iso_version is not None:
        logger.warning(
            'Proxmox "%s" : aucune ISO HTTP pour proxmox.iso — ré-extraire l’ISO '
            "(crée boot/…/proxmox-netboot.iso) ou laisser l’ISO dans isos-ipxe.",
            getattr(iso_version, "version_label", "?"),
        )

    if mode == "dual_initrd":
        return {
            "proxmox_boot_mode": mode,
            "proxmox_iso_url": manual_url,
            "proxmox_iso_autoinstall_url": autoinstall_url or manual_url,
        }
    return {
        "proxmox_boot_mode": mode,
        "proxmox_iso_url": "",
        "proxmox_iso_autoinstall_url": "",
    }


def _proxmox_initrd_on_disk(be: BootEntry | None, cfg: Settings) -> Path | None:
    if not be or not be.initrd_path:
        return None
    rel = be.initrd_path.replace("\\", "/").lstrip("/")
    if rel.startswith("boot/"):
        rel = rel[5:]
    p = cfg.boot_dir / rel
    return p if p.is_file() else None


def _proxmox_menu_fields(
    iso_version: IsoVersion | None,
    be: BootEntry | None,
    cfg: Settings,
) -> dict[str, str]:
    if not be:
        return {
            "proxmox_boot_mode": "single",
            "proxmox_iso_url": "",
            "proxmox_iso_autoinstall_url": "",
        }
    initrd_p = _proxmox_initrd_on_disk(be, cfg)
    if initrd_p:
        from app.services.iso_extractor import _ensure_proxmox_initrd_gzip_for_ipxe

        _ensure_proxmox_initrd_gzip_for_ipxe(initrd_p)
    _ensure_proxmox_netboot_iso_published(iso_version, be, cfg)
    return _resolve_proxmox_boot(iso_version, be, cfg)


def _ensure_proxmox_netboot_iso_published(
    iso_version: IsoVersion | None,
    be: BootEntry | None,
    cfg: Settings,
) -> None:
    """Crée proxmox-netboot.iso sous netboot/ si absent (ne touche pas à l’ISO autoinstall)."""
    if not iso_version or not be:
        return
    from app.services.iso_extractor import (
        PROXMOX_NETBOOT_ISO_BASENAME,
        migrate_legacy_proxmox_netboot_isos,
        publish_proxmox_netboot_iso,
    )

    seg = _boot_os_version_segment(be, "proxmox")
    if not seg:
        return
    dest = cfg.boot_dir / "proxmox" / seg
    netboot_dir = migrate_legacy_proxmox_netboot_isos(dest)
    manual = netboot_dir / PROXMOX_NETBOOT_ISO_BASENAME
    if manual.is_file():
        return
    raw = (iso_version.iso_path or "").strip()
    if raw:
        try:
            p = Path(raw)
            if p.is_file():
                publish_proxmox_netboot_iso(
                    p, dest, invalidate_autoinstall=False
                )
                return
        except OSError:
            pass
    pack = Path(cfg.iso_root) / "proxmox" / str(iso_version.id)
    if pack.is_dir():
        for p in sorted(pack.glob("*.iso")):
            if p.is_file():
                publish_proxmox_netboot_iso(
                    p, dest, invalidate_autoinstall=False
                )
                return


def _append_ubuntu_http_casper_args(
    args: str, cfg: Settings, iso_url: str | None
) -> str:
    """Args noyau casper en mode HTTP (sans NFS) : ramdisk, DHCP, ISO optionnelle."""
    bits: list[str] = []
    if not re.search(r"(?:^|\s)root=/dev/ram0(?:\s|$)", args):
        bits.append("root=/dev/ram0")
    if not re.search(r"(?:^|\s)ramdisk_size=", args):
        bits.append(f"ramdisk_size={cfg.ubuntu_ramdisk_size}")
    if not _has_ip_kernel_arg(args):
        bits.insert(0, "ip=dhcp")
    if iso_url and not re.search(r"(?:^|\s)url=", args):
        bits.append(f"url={iso_url}")
    if not bits:
        return args.strip()
    return f"{args} {' '.join(bits)}".strip()


def _build_kernel_args(
    be,
    os_slug: str,
    cfg: Settings,
    nfsroot_pair: str | None = None,
    iso_version: IsoVersion | None = None,
) -> str:
    """
    Concatène les args DB et ajoute modloop (Alpine).

    **Ubuntu** (défaut, sans ``UBUNTU_NFS_ENABLED``) : ``root=/dev/ram0``,
    ``ramdisk_size=…``, ``ip=dhcp``, ``url=`` vers l’ISO HTTP si le fichier est encore
    sur le serveur ; les entrées autoconfig ajoutent ``autoinstall ds=nocloud-net;…``.

    **Ubuntu NFS** : ``boot=casper``, ``netboot=nfs``, ``nfsroot=``, ``nfsopts=`` (casper(7)).

    Pour **Rocky** / **AlmaLinux** / **CentOS** : ``inst.repo=`` vers la racine HTTP de l’ISO extraite.
    Pour **Fedora** (installateur) : ``inst.stage2=`` ; si **live_os** : ``root=live:…/LiveOS/squashfs.img``,
    ``ro``, ``rd.live.image``. Dans tous les cas : ``rd.neednet=1``, ``ip=dhcp`` et ``initrd=<basename>`` si absents.
    """
    args = be.kernel_args if be and be.kernel_args else ""

    if os_slug.lower() == "esxi":
        return args.strip()

    if os_slug == "alpine" and be:
        if "alpine_repo=" not in args:
            custom = (getattr(be, "alpine_repo_url", None) or "").strip()
            repo = custom if custom else ALPINE_REPO_DEFAULT_PUBLIC
            args = f"{args} alpine_repo={repo}".strip()
        if be.modloop_path:
            modloop_url = _http(be.modloop_path, cfg)
            if "modloop=" not in args:
                args = f"{args} modloop={modloop_url}".strip()

    # Debian : inst.repo= (miroir HTTP extrait, liens dists/ préservés)
    if os_slug in _DEBIAN_NETINST_SLUGS and be:
        seg = _boot_os_version_segment(be, os_slug)
        if seg:
            root_url = _http(f"boot/{os_slug}/{seg}/", cfg)
            if root_url and not re.search(r"(?:^|\s)inst\.repo=", args):
                args = f"{args} inst.repo={root_url}".strip()
        if not _has_ip_kernel_arg(args):
            args = f"ip=dhcp {args}".strip()
        if be.initrd_path and not re.search(r"(?:^|\s)initrd=", args):
            init_bn = be.initrd_path.replace("\\", "/").rstrip("/").split("/")[-1]
            if init_bn:
                args = f"{args} initrd={init_bn}".strip()

    # Rocky / Alma / CentOS : inst.repo=  |  Fedora : inst.stage2= + rd.neednet=1
    if os_slug in _EL_ANACONDA_FULL_ISO_SLUGS and be:
        seg = _boot_os_version_segment(be, os_slug)
        if seg:
            root_url = _http(f"boot/{os_slug}/{seg}/", cfg)
            if root_url:
                if os_slug == "fedora" and getattr(be, "live_os", False):
                    squash_url = _http(f"boot/{os_slug}/{seg}/LiveOS/squashfs.img", cfg)
                    if squash_url:
                        if not re.search(r"(?:^|\s)root=live:", args):
                            args = f"{args} root=live:{squash_url}".strip()
                        if not re.search(r"(?:^|\s)ro(?:\s|$)", args):
                            args = f"{args} ro".strip()
                        if "rd.live.image" not in args:
                            args = f"{args} rd.live.image".strip()
                elif os_slug == "fedora":
                    if not re.search(r"(?:^|\s)inst\.stage2=", args):
                        args = f"{args} inst.stage2={root_url}".strip()
                else:
                    if "inst.repo=" not in args:
                        args = f"{args} inst.repo={root_url}".strip()
        elif not seg:
            logger.warning(
                '%s : inst.repo / inst.stage2 non ajouté — chemin kernel/initrd inattendu '
                '(pas de dossier sous "boot/%s/<version>/").',
                os_slug,
                os_slug,
            )
        if os_slug == "fedora" and not re.search(r"(?:^|\s)rd\.neednet=", args):
            args = f"{args} rd.neednet=1".strip()
        if not _has_ip_kernel_arg(args):
            args = f"ip=dhcp {args}".strip()
        # Doc iPXE Fedora : le noyau attend souvent initrd=<nom> sur la même « ligne » args (dracut)
        if be.initrd_path and not re.search(r"(?:^|\s)initrd=", args):
            init_bn = be.initrd_path.replace("\\", "/").rstrip("/").split("/")[-1]
            if init_bn:
                args = f"{args} initrd={init_bn}".strip()

    # Proxmox VE : linux26 + initrd (PXE communautaire + assistant --pxe-loader ipxe)
    if os_slug == "proxmox" and be:
        args = _strip_kernel_arg_tokens(args, (r"\burl=\S+",))
        if getattr(cfg, "proxmox_vga_params", True):
            if not re.search(r"(?:^|\s)vga=", args):
                args = (
                    f"{args} vga=791 video=vesafb:ywrap,mtrr".strip()
                )
        if not _has_ip_kernel_arg(args):
            args = f"ip=dhcp {args}".strip()
        if not re.search(r"(?:^|\s)ramdisk_size=", args):
            args = f"{args} ramdisk_size={cfg.proxmox_ramdisk_size}".strip()
        if not re.search(r"(?:^|\s)rw(?:\s|$)", args):
            args = f"{args} rw".strip()
        if not re.search(r"(?:^|\s)quiet(?:\s|$)", args):
            args = f"{args} quiet".strip()
        if not re.search(r"(?:^|\s)splash=", args):
            args = f"{args} splash=silent".strip()
        if be.initrd_path and not re.search(r"(?:^|\s)initrd=", args):
            init_bn = be.initrd_path.replace("\\", "/").rstrip("/").split("/")[-1]
            if init_bn:
                args = f"{args} initrd={init_bn}".strip()
        return args.strip()

    # Ubuntu : NFS optionnel (UBUNTU_NFS_ENABLED), sinon HTTP autoinstall (défaut).
    if os_slug.lower() == "ubuntu":
        if nfsroot_pair:
            args = _strip_kernel_arg_tokens(args, _UBUNTU_HTTP_KERNEL_TOKENS)
            if "nfsroot=" not in args:
                nfs_bits = ["boot=casper", "netboot=nfs", f"nfsroot={nfsroot_pair}"]
                if not _has_ip_kernel_arg(args):
                    nfs_bits.insert(0, "ip=dhcp")
                opts = cfg.ubuntu_nfs_mount_opts.strip().strip(",").strip()
                if opts and "nfsopts=" not in args:
                    nfs_bits.append(f"nfsopts={opts}")
                args = f"{args} {' '.join(nfs_bits)}".strip()
        else:
            args = _strip_kernel_arg_tokens(args, _UBUNTU_NFS_KERNEL_TOKENS)
            args = _append_ubuntu_http_casper_args(
                args, cfg, _iso_http_url(iso_version, cfg) or None
            )
        return args

    return args.strip()


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
