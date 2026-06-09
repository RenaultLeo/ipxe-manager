#!/usr/bin/env python3
"""Audit i18n : clés, longueur des listes, chaînes encore identiques à l'anglais."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import MESSAGES, SUPPORTED_LOCALES  # noqa: E402

# Identiques EN volontaires (noms propres, chemins, acronymes).
IGNORE_SAME_AS_EN = frozenset(
    {
        "lang.fr",
        "lang.en",
        "lang.de",
        "lang.es",
        "lang.it",
        "lang.pt",
        "nav.isos",
        "nav.firmware",
        "iso.page_title",
        "iso.col_os",
        "sett.path_iso",
        "sett.col_slug",
        "dash.upload_type_iso",
        "menu.col_url",
        "menu.chain_col_url",
        "menu.url_btn",
        "menu.editor",
        "fw.tftp_label",
        "fw.card_bios",
        "fw.status_step_embed",
        "fw.status_step_bios",
        "fw.status_step_efi",
        "iso.upload.boot_wim",
        "iso.upload.modloop_label",
        "boot.role_boot_sdi",
        "boot.role_boot_wim",
        "boot.role_bootmgr",
        "boot.role_initrd",
        "boot.role_modloop",
        "cfg.type_bundle",
        "cfg.tpl_item_preseed",
        "cfg.tpl_item_unattend",
        "cfg.tpl_item_proxmox",
        "cfg.tpl_item_alpine",
        "cfg.tpl_item_ubuntu_pair",
        "admin.service_ok",
        "super.host",
        "dash.quick_config_sub",
        "iso.detail.winpe_th_index",
        "sett.ot_col_max",
        # Cognats valides (identiques à l’anglais dans la langue cible).
        "admin.role_admin",
        "iso.col_version",
        "super.col_name",
        "menu.chain_col_name",
        "iso.upload.optional",
        "dash.col_status",
        "iso.col_status",
        "cfg.type_dd_unattend",
        "dash.status_error",
        "common.error",
        "iso.status_error",
        "dash.col_file",
        "iso.detail.cfg_th_file",
        "dash.disk_total",
    }
)


def main() -> int:
    en = MESSAGES["en"]
    fr = MESSAGES["fr"]
    keys = list(en.keys())
    n = len(keys)

    print(f"Clés EN/FR : {n}")
    if set(fr) != set(en):
        print("ERREUR : fr et en n'ont pas les mêmes clés")
        missing_fr = set(en) - set(fr)
        missing_en = set(fr) - set(en)
        if missing_fr:
            print("  Manquantes en FR:", ", ".join(sorted(missing_fr)[:20]))
        if missing_en:
            print("  Manquantes en EN:", ", ".join(sorted(missing_en)[:20]))
        return 1

    lv = ROOT / "app" / "locale_values"
    en_list = lv / "_en.list.json"
    if en_list.is_file():
        raw = json.loads(en_list.read_text(encoding="utf-8"))
        status = "OK" if len(raw) == n else "DESYNC"
        print(f"_en.list.json : {len(raw)} / {n} [{status}]")
        for code in ("de", "es", "it", "pt"):
            p = lv / f"{code}.list.json"
            if p.is_file():
                ln = len(json.loads(p.read_text(encoding="utf-8")))
                print(f"  {code}.list.json : {ln} [{('OK' if ln == n else 'DESYNC')}]")

    fr_same = [k for k in keys if fr[k] == en[k]]
    print(f"\nFR identique à EN : {len(fr_same)} (souvent termes techniques)")

    for loc in sorted(SUPPORTED_LOCALES - {"fr", "en"}):
        same = [k for k in keys if MESSAGES[loc][k] == en[k]]
        actionable = [k for k in same if k not in IGNORE_SAME_AS_EN]
        print(f"\n{loc.upper()} identique à EN : {len(same)} total, {len(actionable)} à traiter")
        for k in actionable:
            print(f"  {k}: {en[k][:72]!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
