#!/usr/bin/env python3
"""Vérifie les clés t() des templates et détecte textes FR/EN incorrects par locale."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import MESSAGES, SUPPORTED_LOCALES  # noqa: E402

T_KEY = re.compile(r"""t\(\s*['"]([a-z][a-z0-9_.]+)['"]""")
FR_MARKERS = re.compile(
    r"\b(le|la|les|des|une|pour|cette|avec|dans|vous|être|é|è|ê|à|ç|œ|û|î|ô|ù)\b|"
    r"—|«|»|…",
    re.IGNORECASE,
)


def template_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        keys.update(T_KEY.findall(text))
    return keys


def main() -> int:
    en = MESSAGES["en"]
    fr = MESSAGES["fr"]
    tpl = sorted(template_keys())
    missing: dict[str, list[str]] = {loc: [] for loc in SUPPORTED_LOCALES}
    same_en: dict[str, list[str]] = {loc: [] for loc in SUPPORTED_LOCALES if loc not in ("en",)}
    french_in: dict[str, list[str]] = {loc: [] for loc in ("de", "es", "it", "pt")}

    for key in tpl:
        for loc in SUPPORTED_LOCALES:
            if key not in MESSAGES.get(loc, {}):
                missing[loc].append(key)
        for loc in ("de", "es", "it", "pt"):
            val = MESSAGES[loc].get(key, "")
            if val and val == en.get(key):
                same_en[loc].append(key)
            if val and loc != "fr" and FR_MARKERS.search(val) and val != fr.get(key):
                french_in[loc].append(key)

    print(f"Clés t() dans les templates : {len(tpl)}")
    for loc, keys in missing.items():
        if keys:
            print(f"\n{loc.upper()} clés manquantes ({len(keys)}):")
            for k in keys[:30]:
                print(f"  {k}")
            if len(keys) > 30:
                print(f"  … +{len(keys) - 30}")

    prefixes = ("fw.", "sett.", "menu.", "boot.", "iso.", "cfg.", "dash.", "admin.", "super.")
    for loc in ("de", "es", "it", "pt"):
        fw_same = [k for k in same_en[loc] if k.startswith(prefixes)]
        fr_leak = french_in[loc]
        print(f"\n{loc.upper()} encore EN (pages UI) : {len(fw_same)}")
        for k in sorted(fw_same):
            print(f"  {k}: {en[k][:70]!r}")
        if fr_leak:
            print(f"{loc.upper()} soupçon FR ({len(fr_leak)}):")
            for k in sorted(fr_leak)[:25]:
                print(f"  {k}: {MESSAGES[loc][k][:72]!r}")
            if len(fr_leak) > 25:
                print(f"  … +{len(fr_leak) - 25}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
