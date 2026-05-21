"""
Compatibilité — préférer app.services.winpe_scripts.
"""
from __future__ import annotations

from app.models.models import IsoVersion, WinpeInstall
from app.services.winpe_scripts import (
    inject_startnet_into_boot_wim,
    regenerate_winpe_deployment,
)
from app.services.winpe_wim import boot_wim_filesystem_path

__all__ = [
    "regenerate_winpe_deployment",
    "inject_startnet_into_boot_wim",
    "boot_wim_filesystem_path",
    "patch_boot_wim_startnet",
]


def patch_boot_wim_startnet(
    version: IsoVersion,
    install: WinpeInstall | None = None,
    *,
    installs: list[WinpeInstall] | None = None,
) -> Path:
    inst = installs if installs is not None else list(version.winpe_installs or [])
    regenerate_winpe_deployment(version, inst, patch_wim=True)
    return boot_wim_filesystem_path(version)
