#!/usr/bin/env python3
"""Régénère app/locale_values/*.list.json depuis MESSAGES (ordre des clés EN)."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import app.i18n as i18n_mod

    importlib.reload(i18n_mod)
    messages = i18n_mod.MESSAGES

    lv = ROOT / "app" / "locale_values"
    lv.mkdir(parents=True, exist_ok=True)
    keys = list(messages["en"].keys())
    n = len(keys)

    (lv / "_en.list.json").write_text(
        json.dumps([messages["en"][k] for k in keys], ensure_ascii=False),
        encoding="utf-8",
    )
    for code in ("de", "es", "it", "pt"):
        (lv / f"{code}.list.json").write_text(
            json.dumps([messages[code][k] for k in keys], ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"Synced {n} entries to _en.list.json + de/es/it/pt.list.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
