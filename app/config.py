from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    server_base_url: str = "http://192.168.2.6"
    secret_key: str = "changeme_generate_with_openssl_rand_hex_32"
    admin_password: str = "admin"

    database_url: str = "sqlite:////srv/ipxe/app/ipxe.db"
    redis_url: str = "redis://localhost:6379/0"

    tftp_root: str = "/srv/ipxe/tftpboot"
    http_root: str = "/srv/ipxe/http"
    iso_root: str = "/srv/ipxe/isos"

    max_upload_size: int = 53_687_091_200  # 50 GB
    extract_timeout: int = 3600

    @property
    def menus_dir(self) -> Path:
        return Path(self.http_root) / "menus"

    @property
    def boot_dir(self) -> Path:
        return Path(self.http_root) / "boot"

    @property
    def configs_dir(self) -> Path:
        return Path(self.http_root) / "configs"


settings = Settings()
