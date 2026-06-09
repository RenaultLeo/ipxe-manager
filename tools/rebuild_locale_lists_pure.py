#!/usr/bin/env python3
"""
Reconstruit app/locale_values/*.list.json depuis les paires EN des scripts Node
(sans dépendre de l’ordre corrompu des listes actuelles).

Logique identique à tools/build_locale_lists.mjs : map[chaîne EN unique] → traduction.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LV = ROOT / "app" / "locale_values"
TOOLS = ROOT / "tools"

PAIR_IN_ARRAY = re.compile(
    r'\[\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,?\s*\],?',
    re.DOTALL,
)


def _unescape(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _extract_array_pairs(mjs_text: str, const_name: str) -> list[tuple[str, str]]:
    marker = f"const {const_name} = ["
    start = mjs_text.find(marker)
    if start < 0:
        return []
    i = mjs_text.find("[", start)
    depth = 0
    end = -1
    for j in range(i, len(mjs_text)):
        c = mjs_text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = j
                break
    if end < 0:
        return []
    block = mjs_text[i : end + 1]
    pairs: list[tuple[str, str]] = []
    for a, b in PAIR_IN_ARRAY.findall(block):
        pairs.append((_unescape(a), _unescape(b)))
    return pairs


def _load_en_ordered() -> tuple[list[str], list[str]]:
    sys.path.insert(0, str(ROOT))
    from app.i18n import MESSAGES  # noqa: WPS433

    en = MESSAGES["en"]
    keys = list(en.keys())
    values = [en[k] for k in keys]
    return keys, values


def _build_list(en_values: list[str], pairs: list[tuple[str, str]]) -> list[str]:
    uniq = sorted(set(en_values))
    mapping = {s: s for s in uniq}
    for en_s, tr in pairs:
        if en_s not in mapping:
            print(f"WARN: paire inconnue (EN): {en_s[:72]!r}", file=sys.stderr)
        mapping[en_s] = tr
    return [mapping.get(s, s) for s in en_values]


def main() -> int:
    build_mjs = (TOOLS / "build_locale_lists.mjs").read_text(encoding="utf-8")
    sup_mjs = (TOOLS / "locale_pairs_supplement.mjs").read_text(encoding="utf-8")

    keys, en_values = _load_en_ordered()
    n = len(keys)
    LV.mkdir(parents=True, exist_ok=True)
    (LV / "_en.list.json").write_text(
        json.dumps(en_values, ensure_ascii=False),
        encoding="utf-8",
    )

    locales = {
        "de": ("PAIRS_DE", "SUPPLEMENT_DE"),
        "es": ("PAIRS_ES", "SUPPLEMENT_ES"),
        "it": ("PAIRS_IT", "SUPPLEMENT_IT"),
        "pt": ("PAIRS_PT", "SUPPLEMENT_PT"),
    }
    for code, (pairs_name, sup_name) in locales.items():
        pairs = _extract_array_pairs(build_mjs, pairs_name)
        pairs += _extract_array_pairs(sup_mjs, sup_name)
        out = _build_list(en_values, pairs)
        overrides_path = LV / f"{code}.keys.json"
        if overrides_path.is_file():
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            keyed = dict(zip(keys, out))
            keyed.update({str(k): str(v) for k, v in overrides.items()})
            out = [keyed[k] for k in keys]
        (LV / f"{code}.list.json").write_text(
            json.dumps(out, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"{code}: {len(out)} entrées, {len(pairs)} paires")

    if len(en_values) != n:
        print("ERREUR: taille incohérente", file=sys.stderr)
        return 1
    print(f"OK — {n} clés, _en + de/es/it/pt.list.json régénérés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
