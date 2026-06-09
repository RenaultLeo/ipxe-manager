#!/usr/bin/env python3
"""Génère des entrées gaps pour clés encore identiques à l'anglais mais présentes dans les paires MJS."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.i18n import MESSAGES  # noqa: E402

PAIR = re.compile(
    r'\[\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,?\s*\],?',
    re.DOTALL,
)

LOCALE_MARKERS = {
    "de": ("const PAIRS_DE = [", "export const SUPPLEMENT_DE = ["),
    "es": ("const PAIRS_ES = [", "export const SUPPLEMENT_ES = ["),
    "it": ("const PAIRS_IT = [", "export const SUPPLEMENT_IT = ["),
    "pt": ("const PAIRS_PT = [", "export const SUPPLEMENT_PT = ["),
}


def _unesc(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _load_pairs(mjs: str, marker: str) -> dict[str, str]:
    start = mjs.find(marker)
    if start < 0:
        return {}
    i = mjs.find("[", start)
    depth = 0
    end = -1
    for j in range(i, len(mjs)):
        if mjs[j] == "[":
            depth += 1
        elif mjs[j] == "]":
            depth -= 1
            if depth == 0:
                end = j
                break
    if end < 0:
        return {}
    pairs: dict[str, str] = {}
    for a, b in PAIR.findall(mjs[i : end + 1]):
        pairs[_unesc(a)] = _unesc(b)
    return pairs


def load_locale_pairs() -> dict[str, dict[str, str]]:
    build = (ROOT / "tools" / "build_locale_lists.mjs").read_text(encoding="utf-8")
    sup = (ROOT / "tools" / "locale_pairs_supplement.mjs").read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for code, (main_m, sup_m) in LOCALE_MARKERS.items():
        pairs = _load_pairs(build, main_m)
        pairs.update(_load_pairs(sup, sup_m))
        out[code] = pairs
    return out


def build_auto_gaps() -> dict[str, dict[str, str]]:
    pairs_by_loc = load_locale_pairs()
    en = MESSAGES["en"]
    gaps: dict[str, dict[str, str]] = {}
    for key, en_val in en.items():
        if not en_val:
            continue
        row: dict[str, str] = {}
        for loc in ("de", "es", "it", "pt"):
            cur = MESSAGES[loc].get(key, "")
            if cur and cur != en_val:
                continue
            tr = pairs_by_loc[loc].get(en_val)
            if tr and tr != en_val:
                row[loc] = tr
        if row:
            gaps[key] = row
    return gaps
