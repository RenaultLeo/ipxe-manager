from __future__ import annotations

import logging
from pathlib import Path
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def detect_primary_ipv4() -> str:
    """IPv4 joignable pour les clients PXE si ``SERVER_BASE_URL`` n’expose pas d’hôte utilisable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def default_server_base_url() -> str:
    """Si ``SERVER_BASE_URL`` absent du .env, URL publique = HTTP + IP/route locale."""
    return f"http://{detect_primary_ipv4()}"


def resolve_server_base_url(db: Session | None = None) -> str:
    """
    URL de base effective : valeur Paramètres (BDD) si présente, sinon ``settings`` / ``.env``.
    """
    from app.models.models import AppSetting

    own_session = False
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
        own_session = True
    try:
        row = db.query(AppSetting).filter(AppSetting.key == "server_base_url").first()
        if row and row.value and str(row.value).strip():
            return str(row.value).strip().rstrip("/")
    finally:
        if own_session:
            db.close()
    return settings.server_base_url.rstrip("/")


def sync_settings_server_base_url_from_db() -> None:
    """Aligne le singleton ``settings`` sur la BDD (appel au démarrage uvicorn)."""
    settings.server_base_url = resolve_server_base_url()


def _write_env_server_base_url(url: str) -> None:
    """Met à jour ``SERVER_BASE_URL`` dans ``.env`` si le fichier existe (Celery / ``Settings()``)."""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        found = False
        for line in lines:
            if line.strip().startswith("SERVER_BASE_URL="):
                out.append(f"SERVER_BASE_URL={url}")
                found = True
            else:
                out.append(line)
        if not found:
            if out and out[-1].strip():
                out.append("")
            out.append(f"SERVER_BASE_URL={url}")
        env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Impossible de mettre à jour SERVER_BASE_URL dans .env : %s", exc)


def persist_server_base_url(db: Session, url: str) -> str:
    """Enregistre l’URL (BDD + ``.env`` + singleton) — source unique pour menus et Celery."""
    from app.models.models import AppSetting

    normalized = url.strip().rstrip("/")
    row = db.query(AppSetting).filter(AppSetting.key == "server_base_url").first()
    if row:
        row.value = normalized
    else:
        db.add(AppSetting(key="server_base_url", value=normalized))
    db.commit()
    settings.server_base_url = normalized
    _write_env_server_base_url(normalized)
    return normalized


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        # Clés obsolètes dans un vieux .env (ex. retirées du schéma) : ne pas planter.
        extra="ignore",
    )

    server_base_url: str = Field(default_factory=default_server_base_url)
    secret_key: str = "changeme_generate_with_openssl_rand_hex_32"
    admin_password: str = "admin"

    database_url: str = "sqlite:////srv/ipxe/app/ipxe.db"
    redis_url: str = "redis://localhost:6379/0"

    tftp_root: str = "/srv/ipxe/tftpboot"
    http_root: str = "/srv/ipxe/http"
    iso_root: str = "/srv/ipxe/isos"
    # Segment d’URL Nginx pour servir ISO_ROOT (ex. location /isos-ipxe/ → alias ISO_ROOT).
    # Évite tout chevauchement avec les routes web « /isos » de l’application.
    iso_http_alias: str = "isos-ipxe"
    build_dir: str = "/srv/ipxe/build"   # répertoire de compilation firmware iPXE

    upload_min_free_bytes: int = 268_435_456  # 256 Mo — garde fou avant uploads (multipart + fichiers boot)

    max_upload_size: int = 53_687_091_200  # 50 GB
    extract_timeout: int = 3600

    # Ubuntu : par défaut HTTP (root=/dev/ram0, url= ISO, autoinstall nocloud-net). NFS optionnel.
    ubuntu_nfs_enabled: bool = False
    ubuntu_nfs_host: str = ""  # Vide : hôte dérivé de SERVER_BASE_URL puis IPv4 locale
    ubuntu_nfs_mount_opts: str = "vers=4,tcp"  # Passé en nfsopts= (casper), pas après une virgule dans nfsroot
    ubuntu_ramdisk_size: int = 1_500_000  # Paramètre noyau ramdisk_size= (autoinstall HTTP)

    @property
    def ipxe_src_dir(self) -> Path:
        return Path(self.build_dir) / "ipxe-src"

    @property
    def menus_dir(self) -> Path:
        return Path(self.http_root) / "menus"

    @property
    def boot_dir(self) -> Path:
        return Path(self.http_root) / "boot"

    @property
    def configs_dir(self) -> Path:
        return Path(self.http_root) / "configs"

    def iso_public_http_url(self, fs_path: str | Path | None) -> str:
        """URL HTTP absolue pour un fichier sous ``iso_root`` (menus iPXE / scripts utilisateur)."""
        if fs_path is None:
            return ""
        raw = str(fs_path).strip()
        if not raw:
            return ""
        try:
            abs_iso = Path(raw).expanduser().resolve()
            root = Path(self.iso_root).expanduser().resolve()
            rel = abs_iso.relative_to(root).as_posix()
        except (ValueError, OSError):
            return ""
        seg = self.iso_http_alias.strip().strip("/")
        if not seg:
            return ""
        base = resolve_server_base_url().rstrip("/")
        return f"{base}/{seg}/{rel}"

    def ubuntu_nfs_server_hostname(self) -> str | None:
        """Hostname ou IP utilisé dans nfsroot= (côté client)."""
        if not self.ubuntu_nfs_enabled:
            return None
        manual = self.ubuntu_nfs_host.strip()
        if manual:
            return manual.rstrip("/")
        host = urlparse(resolve_server_base_url().strip()).hostname
        if host:
            hl = host.lower()
            if hl not in ("localhost", "127.0.0.1", "::1"):
                return host
        return detect_primary_ipv4()

    def ubuntu_boot_version_dir(self, version_slug: str) -> Path:
        """Répertoire disque HTTP_ROOT/boot/ubuntu/<slug> (sans resolve)."""
        slug = version_slug.strip().replace("\\", "/").lstrip("/")
        if "/" in slug:
            slug = slug.split("/")[0]
        return self.boot_dir / "ubuntu" / slug

    def ubuntu_nfsroot_pair(self, os_slug: str, version_slug: str) -> str | None:
        """
        Partie « serveur:chemin » du paramètre noyau nfsroot= (sans options après une virgule).

        Casper / Ubuntu live attend les options NFS dans un paramètre séparé ``nfsopts=…``
        (voir casper(7)) ; mettre ``,vers=3`` dans nfsroot peut provoquer un ENOENT côté client.
        """
        if os_slug.lower() != "ubuntu" or not self.ubuntu_nfs_enabled:
            return None
        host = self.ubuntu_nfs_server_hostname()
        if not host:
            return None
        path = self.ubuntu_boot_version_dir(version_slug).resolve().as_posix()
        return f"{host}:{path}"


settings = Settings()
