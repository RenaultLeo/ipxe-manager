from pathlib import Path
import socket
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def detect_primary_ipv4() -> str:
    """IPv4 joignable pour les PXE si ``SERVER_BASE_URL`` n’expose pas d’hôte utilisable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # IPv4 « sortante » selon la table de routage (UDP, pas de transfert nécessaire).
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
    """Si ``SERVER_BASE_URL`` absent du .env, URL du serveur = IP/route locale + HTTPS."""
    return f"https://{detect_primary_ipv4()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
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

    # PEM utilisé lors du ``make CERT=…`` (cert présenté par Nginx pour HTTPS).
    # Par défaut : certificat serveur généré par deploy/https_cert_gen.sh (voir deploy/setup.sh).
    ipxe_tls_trusted_pem: str = "/srv/ipxe/certs/ipxe-manager/server.crt"

    upload_min_free_bytes: int = 268_435_456  # 256 Mo — garde fou avant uploads (multipart + fichiers boot)

    max_upload_size: int = 53_687_091_200  # 50 GB
    extract_timeout: int = 3600

    # Ubuntu full-ISO sous http/boot/ubuntu/<slug> — live-server lit le contenu via NFS (comme SMB côté Windows)
    ubuntu_nfs_enabled: bool = False
    ubuntu_nfs_host: str = ""  # Vide : hôte = celui de SERVER_BASE_URL puis IP locale
    ubuntu_nfs_mount_opts: str = "vers=4,tcp"  # Passé en nfsopts= (casper), pas après une virgule dans nfsroot

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
        base = self.server_base_url.rstrip("/")
        return f"{base}/{seg}/{rel}"

    def ubuntu_nfs_server_hostname(self) -> str | None:
        """Hostname ou IP utilisé dans nfsroot= (côté client)."""
        if not self.ubuntu_nfs_enabled:
            return None
        manual = self.ubuntu_nfs_host.strip()
        if manual:
            return manual.rstrip("/")
        host = urlparse(self.server_base_url.strip()).hostname
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
