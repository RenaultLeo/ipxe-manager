from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, BigInteger,
    DateTime, ForeignKey, Boolean,
)
from sqlalchemy.orm import relationship
from app.database import Base


class OsType(Base):
    __tablename__ = "os_types"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(32), unique=True, nullable=False)   # windows, ubuntu, debian …
    label = Column(String(64), nullable=False)               # Windows, Ubuntu, Debian …
    icon = Column(String(64), default="bi-hdd")             # Bootstrap Icon class
    boot_type  = Column(String(16), default="linux")          # linux | windows | custom
    is_builtin = Column(Boolean, default=False)               # True = OS de base, type de config forcé
    created_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship("IsoVersion", back_populates="os_type", cascade="all, delete")


class IsoVersion(Base):
    __tablename__ = "iso_versions"

    id = Column(Integer, primary_key=True, index=True)
    os_type_id = Column(Integer, ForeignKey("os_types.id"), nullable=False)
    version_label = Column(String(64), nullable=False)       # "22.04 LTS", "11 Bullseye"…
    status = Column(String(16), default="uploaded")          # uploaded|extracting|ready|error
    iso_path = Column(String(512))
    iso_size = Column(BigInteger, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    os_type = relationship("OsType", back_populates="versions")
    boot_entry = relationship("BootEntry", back_populates="iso_version", uselist=False, cascade="all, delete")
    autoconfigs = relationship("AutoConfig", back_populates="iso_version", cascade="all, delete")


class BootEntry(Base):
    __tablename__ = "boot_entries"

    id = Column(Integer, primary_key=True, index=True)
    iso_version_id = Column(Integer, ForeignKey("iso_versions.id"), nullable=False, unique=True)

    # Linux
    kernel_path = Column(String(512))      # relative path under http_root/boot/<os>/<id>/
    initrd_path = Column(String(512))
    kernel_args = Column(Text, default="")

    # Windows / WinPE
    boot_wim_path = Column(String(512))   # sources/boot.wim
    bcd_path      = Column(String(512))   # boot/BCD
    boot_sdi_path = Column(String(512))   # boot/boot.sdi
    bootmgr_path  = Column(String(512))   # bootmgr.efi (UEFI)

    # EFI / UEFI
    efi_path = Column(String(512))

    # Alpine Linux — modloop (module loop filesystem)
    modloop_path = Column(String(512))

    # Script iPXE personnalisé (optionnel — chainload custom)
    custom_ipxe_path = Column(String(512))

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    iso_version = relationship("IsoVersion", back_populates="boot_entry")


class AutoConfig(Base):
    __tablename__ = "autoconfigs"

    id = Column(Integer, primary_key=True, index=True)
    iso_version_id = Column(Integer, ForeignKey("iso_versions.id"), nullable=False)
    config_type = Column(String(32))   # preseed | kickstart | unattend | cloud-init | custom
    label = Column(String(128), default="")
    content = Column(Text, default="")            # Ubuntu cloud-init bundle : corps user-data
    meta_data_content = Column(Text, default="")  # Ubuntu bundle : corps meta-data
    ubuntu_cloud_slug = Column(String(128))         # dossier conf-cloudInit-<slug> si bundle
    file_path = Column(String(512))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    iso_version = relationship("IsoVersion", back_populates="autoconfigs")


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(256))
    file_type = Column(String(32))   # iso|kernel|initrd|boot_wim|ipxe|config|other
    size = Column(BigInteger, default=0)
    status = Column(String(16), default="pending")  # pending|processing|done|error
    task_id = Column(String(128))
    error_msg = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(128), unique=True, nullable=False)
    value = Column(Text, default="")


class RemoteChain(Base):
    """Chainload vers un menu iPXE distant (autre serveur)."""
    __tablename__ = "remote_chains"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(128), nullable=False)   # Nom affiché dans le menu
    url        = Column(String(512), nullable=False)   # URL exacte du menu distant
    enabled    = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
