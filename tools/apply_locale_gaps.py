#!/usr/bin/env python3
"""Fusionne tools/locale_gaps.json dans app/locale_values/*.keys.json puis resynchronise les listes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAPS_PATH = ROOT / "tools" / "locale_gaps.json"
LOCALE_VALUES = ROOT / "app" / "locale_values"


def main() -> int:
    if not GAPS_PATH.is_file():
        print(f"Fichier introuvable : {GAPS_PATH}", file=sys.stderr)
        return 1

    gaps: dict[str, dict[str, str]] = json.loads(GAPS_PATH.read_text(encoding="utf-8"))
    for code in ("de", "es", "it", "pt"):
        path = LOCALE_VALUES / f"{code}.keys.json"
        current: dict[str, str] = {}
        if path.is_file():
            current = json.loads(path.read_text(encoding="utf-8"))
        updated = 0
        for key, locales in gaps.items():
            value = locales.get(code)
            if value is None:
                continue
            if current.get(key) != value:
                current[key] = value
                updated += 1
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{code}.keys.json : {updated} entrée(s) mise(s) à jour ({len(current)} au total)")

    sync = ROOT / "tools" / "sync_locale_lists.py"
    return subprocess.call([sys.executable, str(sync)], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
