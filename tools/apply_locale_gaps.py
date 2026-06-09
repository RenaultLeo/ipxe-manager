#!/usr/bin/env python3
"""Régénère locale_gaps.json, réécrit *.keys.json et reconstruit les listes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    for script in ("tools/build_locale_gaps.py", "tools/sync_keys_from_gaps.py"):
        code = subprocess.call([sys.executable, str(ROOT / script)], cwd=ROOT)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
