from __future__ import annotations

import re
from pathlib import Path

from app.config import resolve_server_base_url, settings
from app.models.models import AutoConfig, IsoVersion

_KERNEL_OPT_RE = re.compile(r"^\s*kernelopt\s*=", re.I)


def _cfg_path_from_rel(rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(settings.http_root) / str(rel).lstrip("/").replace("\\", "/")
    return p if p.is_file() else None


def _merge_kernelopt(existing: str, ks_url: str | None) -> str:
    tokens: list[str] = []
    for tok in (existing or "").split():
        t = tok.strip()
        if not t:
            continue
        tl = t.lower()
        if tl == "cdromboot" or tl.startswith("ks="):
            continue
        tokens.append(t)
    if not any(t.lower() == "runweasel" for t in tokens):
        tokens.insert(0, "runweasel")
    if ks_url:
        tokens.append(f"ks={ks_url}")
    return " ".join(tokens).strip()


def _rewrite_boot_cfg_kernelopt(text: str, ks_url: str | None) -> str:
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if _KERNEL_OPT_RE.match(line):
            cur = line.split("=", 1)[1].strip() if "=" in line else ""
            out.append(f"kernelopt={_merge_kernelopt(cur, ks_url)}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"kernelopt={_merge_kernelopt('', ks_url)}")
    return "\n".join(out).rstrip() + "\n"


def _target_boot_cfg_paths(version: IsoVersion) -> list[Path]:
    be = version.boot_entry
    if not be:
        return []
    targets: list[Path] = []
    efi = _cfg_path_from_rel(getattr(be, "esxi_boot_cfg_path", None))
    legacy = _cfg_path_from_rel(
        getattr(be, "esxi_boot_cfg_legacy_path", None) or getattr(be, "esxi_boot_cfg_path", None)
    )
    for p in (efi, legacy):
        if p and p not in targets:
            targets.append(p)
    return targets


def activate_esxi_kickstart(db, version: IsoVersion, cfg: AutoConfig) -> None:
    if (version.os_type.slug or "").lower() != "esxi":
        raise ValueError("Activation ESXi réservée aux versions ESXi.")
    if cfg.iso_version_id != version.id:
        raise ValueError("Cette config n'appartient pas à cette version ESXi.")
    if cfg.config_type not in ("esxi-kickstart", "kickstart"):
        raise ValueError("Type de config incompatible ESXi (attendu esxi-kickstart).")
    if not (cfg.file_path or "").strip():
        raise ValueError("Chemin config vide.")
    targets = _target_boot_cfg_paths(version)
    if not targets:
        raise FileNotFoundError("ipxe-boot.cfg ESXi introuvable (extraire l'ISO d'abord).")
    ks_url = f"{resolve_server_base_url(db).rstrip('/')}/{cfg.file_path.lstrip('/')}"
    for p in targets:
        raw = p.read_text(encoding="utf-8", errors="replace")
        p.write_text(_rewrite_boot_cfg_kernelopt(raw, ks_url), encoding="utf-8")
    version.active_autoconfig_id = cfg.id
    db.add(version)
    db.commit()


def clear_active_esxi_kickstart(db, version: IsoVersion) -> None:
    if (version.os_type.slug or "").lower() != "esxi":
        return
    for p in _target_boot_cfg_paths(version):
        raw = p.read_text(encoding="utf-8", errors="replace")
        p.write_text(_rewrite_boot_cfg_kernelopt(raw, None), encoding="utf-8")
    version.active_autoconfig_id = None
    db.add(version)
    db.commit()

