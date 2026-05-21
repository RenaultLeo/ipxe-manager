"""
Injection startnet.cmd dans boot.wim — délègue à winpe_scripts (scripts PowerShell générés).
"""
from __future__ import annotations

from app.models.models import IsoVersion, WinpeInstall
from app.services.winpe_scripts import (
    inject_startnet_into_boot_wim,
    regenerate_winpe_deployment,
)

__all__ = [
    "regenerate_winpe_deployment",
    "inject_startnet_into_boot_wim",
    "patch_boot_wim_startnet",
]


def patch_boot_wim_startnet(
    version: IsoVersion,
    install: WinpeInstall | None = None,
    *,
    installs: list[WinpeInstall] | None = None,
) -> object:
    """Compat : régénère scripts + startnet pour tous les masters de la version."""
    inst = installs if installs is not None else list(version.winpe_installs or [])
    regenerate_winpe_deployment(version, inst, patch_wim=True)
    from app.services.winpe_scripts import boot_wim_filesystem_path

    return boot_wim_filesystem_path(version)
