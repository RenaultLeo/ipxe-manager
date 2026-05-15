from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
    )

    server_base_url: str = "http://192.168.2.6"
    secret_key: str = "changeme_generate_with_openssl_rand_hex_32"
    admin_password: str = "admin"

    database_url: str = "sqlite:////srv/ipxe/app/ipxe.db"
    redis_url: str = "redis://localhost:6379/0"

    tftp_root: str = "/srv/ipxe/tftpboot"
    http_root: str = "/srv/ipxe/http"
    iso_root: str = "/srv/ipxe/isos"
    build_dir: str = "/srv/ipxe/build"   # répertoire de compilation firmware iPXE

    max_upload_size: int = 53_687_091_200  # 50 GB
    extract_timeout: int = 3600

    # Ubuntu full-ISO sous http/boot/ubuntu/<slug> — live-server lit le contenu via NFS (comme SMB côté Windows)
    ubuntu_nfs_enabled: bool = False
    ubuntu_nfs_host: str = ""  # Vide = hôte tiré de SERVER_BASE_URL ; sinon IP/hostname NFS atteignable par les clients
    ubuntu_nfs_mount_opts: str = "vers=4,tcp"  # Suffix après host:chemin dans nfsroot=

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

    def ubuntu_nfs_server_hostname(self) -> str | None:
        """Hostname ou IP utilisé dans nfsroot= (côté client)."""
        if not self.ubuntu_nfs_enabled:
            return None
        manual = self.ubuntu_nfs_host.strip()
        if manual:
            return manual.rstrip("/")
        parsed = urlparse(self.server_base_url)
        host = parsed.hostname
        return host.strip() if host else None

    def ubuntu_nfsroot_pair(self, os_slug: str, version_slug: str) -> str | None:
        """
        Valeur après nfsroot= : host:/chemin/absolu(,opts).
        Chaque version a son dossier d’extraction sous boot/<slug>/<version_slug>.
        """
        if os_slug.lower() != "ubuntu" or not self.ubuntu_nfs_enabled:
            return None
        host = self.ubuntu_nfs_server_hostname()
        if not host:
            return None
        root = self.boot_dir / "ubuntu" / version_slug
        path = root.resolve().as_posix()
        opts = self.ubuntu_nfs_mount_opts.strip().strip(",").strip()
        base = f"{host}:{path}"
        return f"{base},{opts}" if opts else base


settings = Settings()
