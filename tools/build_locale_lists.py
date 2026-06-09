#!/usr/bin/env python3
"""Régénère app/locale_values/*.list.json (nécessite Node.js)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    node = shutil.which("node")
    if not node:
        print(
            "Node.js introuvable — repli Python (locale_gaps + sync_locale_lists).",
            file=sys.stderr,
        )
        return sync_without_node()
    for script in ("tools/extract_en_list.mjs", "tools/build_locale_lists.mjs"):
        r = subprocess.run([node, script], cwd=ROOT)
        if r.returncode != 0:
            return r.returncode
    pure = ROOT / "tools" / "rebuild_locale_lists_pure.py"
    r = subprocess.run([sys.executable, str(pure)], cwd=ROOT)
    if r.returncode != 0:
        return r.returncode
    print("Locales DE/ES/IT/PT régénérées sous app/locale_values/")
    return 0


def sync_without_node() -> int:
    """Repli sans Node : paires MJS + locale_gaps.json."""
    pure = ROOT / "tools" / "rebuild_locale_lists_pure.py"
    return subprocess.run([sys.executable, str(pure)], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
