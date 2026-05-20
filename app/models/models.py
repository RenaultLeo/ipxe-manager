from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, BigInteger,
    DateTime, ForeignKey, Boolean,
)
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), default="user", nullable=False)  # admin | user
    created_at = Column(DateTime, default=datetime.utcnow)


class OsType(Base):
    __tablename__ = "os_types"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(32), unique=True, nullable=False)   # windows, ubuntu, debian …
    label = Column(String(64), nullable=False)               # Windows, Ubuntu, Debian …
    icon = Column(String(64), default="bi-hdd")             # Bootstrap Icon class
    boot_type  = Column(String(16), default="linux")          # linux | windows | tools | esxi | custom…
    is_builtin = Column(Boolean, default=False)               # True = OS de base, type de config forcé

    # Ordre d'affichage (menus iPXE, onglets ISO, liste paramètres) — modifiable au glisser-déposer.
    ui_sort_order = Column(Integer, default=0, nullable=False)
    # Afficher la carte correspondante sur le tableau de bord.
    show_on_dashboard = Column(Boolean, default=True, nullable=False)

    extract_full_iso = Column(Boolean, default=False)       # extraction 7z complète vers boot/<os>/<ver>/
    extract_paths_json = Column(Text, default="[]")  # [{ "filename":"vmlinuz","max":1 }] ou legacy {"pattern":...}
    ipxe_roles_json = Column(Text, default="[]")  # obsolète formulaire — conservé pour anciennes entrées
    forced_autoconfig_type = Column(String(64))  # type de config AutoConfig imposé (OS non built-in)

    created_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship("IsoVersion", back_populates="os_type", cascade="all, delete")


class IsoVersion(Base):
    __tablename__ = "iso_versions"

    id = Column(Integer, primary_key=True, index=True)
    os_type_id = Column(Integer, ForeignKey("os_types.id"), nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    version_label = Column(String(64), nullable=False)       # "22.04 LTS", "11 Bullseye"…
    status = Column(String(16), default="uploaded")          # uploaded|extracting|ready|error
    iso_path = Column(String(512))
    iso_size = Column(BigInteger, default=0)
    notes = Column(Text, default="")
    iso_was_extracted = Column(Boolean, default=False)  # True après au moins une extraction ISO réussie
    delete_iso_after_next_extract = Column(Boolean, default=False)  # purge disque après prochain extract OK
    ubuntu_nfs_boot = Column(Boolean, default=False)  # menu iPXE : netboot=nfs au lieu de HTTP autoinstall
    extract_basename_report_json = Column(Text, default="")  # dernier rapport recherche par nom { "init": ["a/b",…] }
    active_autoconfig_id = Column(Integer, ForeignKey("autoconfigs.id"), nullable=True, index=True)
    active_winpe_install_id = Column(Integer, ForeignKey("winpe_installs.id"), nullable=True, index=True)
    winpe_startnet_patched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    os_type = relationship("OsType", back_populates="versions")
    boot_entry = relationship("BootEntry", back_populates="iso_version", uselist=False, cascade="all, delete")
    autoconfigs = relationship(
        "AutoConfig",
        back_populates="iso_version",
        cascade="all, delete",
        foreign_keys="AutoConfig.iso_version_id",
    )
    # Deux liens vers WinpeInstall (liste + active_winpe_install_id) : primaryjoin explicites
    winpe_installs = relationship(
        "WinpeInstall",
        back_populates="iso_version",
        cascade="all, delete",
        foreign_keys="WinpeInstall.iso_version_id",
    )
    active_winpe_install = relationship(
        "WinpeInstall",
        primaryjoin="IsoVersion.active_winpe_install_id == WinpeInstall.id",
        foreign_keys="IsoVersion.active_winpe_install_id",
        uselist=False,
        post_update=True,
        viewonly=True,
    )


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
    # Alpine : URL du dépôt APK (vide = CDN public par défaut dans menu_generator)
    alpine_repo_url = Column(String(512))

    # Fedora ISO Live (Workstation) : root=live:http://…/LiveOS/squashfs.img au lieu de inst.stage2
    live_os = Column(Boolean, default=False)

    # Script iPXE personnalisé (optionnel — chainload custom)
    custom_ipxe_path = Column(String(512))

    # Upload manuel : artefacts Linux au-delà de kernel/initrd (ex. NixOS « init »)
    extra_linux_paths_json = Column(Text, default="[]")  # [{"basename":"init","path":"boot/os/ver/init"},…]

    # VMware ESXi — UEFI : esxi_efi_boot_path → mboot.efi (copie de bootx64.efi, doc VMware)
    # Legacy : kernel_path → mboot.c32
    # ESXi : ipxe-boot.cfg + JSON esxi_modules (UEFI mboot.efi et Legacy mboot.c32 utilisent les mêmes URLs).
    esxi_boot_cfg_path = Column(String(512))
    esxi_boot_cfg_legacy_path = Column(String(512))
    esxi_efi_boot_path = Column(String(512))
    esxi_modules = Column(Text, default="")
    esxi_modules_legacy = Column(Text, default="")

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    iso_version = relationship("IsoVersion", back_populates="boot_entry")


class WinpeInstall(Base):
    """Image Windows ``install.wim`` sous ``boot/<os>/<ver>/installs/<slug>/install.wim``."""
    __tablename__ = "winpe_installs"

    id = Column(Integer, primary_key=True, index=True)
    iso_version_id = Column(Integer, ForeignKey("iso_versions.id"), nullable=False, index=True)
    slug = Column(String(128), nullable=False)
    label = Column(String(128), default="")
    wim_index = Column(Integer, default=1)  # index DISM dans install.wim
    created_at = Column(DateTime, default=datetime.utcnow)

    iso_version = relationship(
        "IsoVersion",
        back_populates="winpe_installs",
        foreign_keys=[iso_version_id],
    )


class AutoConfig(Base):
    __tablename__ = "autoconfigs"

    id = Column(Integer, primary_key=True, index=True)
    iso_version_id = Column(Integer, ForeignKey("iso_versions.id"), nullable=False)
    config_type = Column(String(64))   # prédefini (preseed, …) ou type personnalisé (slug utilisateur)
    label = Column(String(128), default="")
    content = Column(Text, default="")            # Ubuntu cloud-init bundle : corps user-data
    meta_data_content = Column(Text, default="")  # Ubuntu bundle : corps meta-data
    ubuntu_cloud_slug = Column(String(128))         # dossier conf-cloudInit-<slug> si bundle
    file_path = Column(String(512))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    iso_version = relationship(
        "IsoVersion",
        back_populates="autoconfigs",
        foreign_keys=[iso_version_id],
    )


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
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
