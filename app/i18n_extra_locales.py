"""Charge les locales DE / ES / IT / PT (fichiers ``locale_values/*.list.json``)."""
from __future__ import annotations

import json
from pathlib import Path


def _value_tuple(code: str) -> tuple[str, ...]:
    p = Path(__file__).resolve().parent / "locale_values" / f"{code}.list.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{p}: expected JSON array")
    return tuple(str(x) for x in raw)


def merge_extra_locales(messages: dict[str, dict[str, str]]) -> None:
    en = messages["en"]
    keys = list(en.keys())
    n = len(keys)
    for code in ("de", "es", "it", "pt"):
        vals = _value_tuple(code)
        if len(vals) != n:
            raise ValueError(
                f"Locale {code}: {len(vals)} chaînes, attendu {n} (aligné sur en)"
            )
        merged = dict(zip(keys, vals))
        if len(merged) != len(keys):
            raise RuntimeError(f"i18n {code}: duplicate key in zip")
        messages[code] = merged
