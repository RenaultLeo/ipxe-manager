#!/usr/bin/env python3
"""Réécrit *.keys.json uniquement depuis tools/locale_gaps.json (évite les overrides corrompus)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAPS = ROOT / "tools" / "locale_gaps.json"
LV = ROOT / "app" / "locale_values"


def main() -> int:
    gaps: dict[str, dict[str, str]] = json.loads(GAPS.read_text(encoding="utf-8"))
    for code in ("de", "es", "it", "pt"):
        keyed = {
            key: locales[code]
            for key, locales in gaps.items()
            if code in locales
        }
        path = LV / f"{code}.keys.json"
        path.write_text(
            json.dumps(keyed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{code}.keys.json : {len(keyed)} entrées")
    rebuild = ROOT / "tools" / "rebuild_locale_lists_pure.py"
    return subprocess.call([sys.executable, str(rebuild)], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
