"""Charge les locales DE / ES / IT / PT (fichiers ``locale_values/*.list.json``)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _value_tuple(code: str, en: dict[str, str]) -> tuple[str, ...]:
    p = Path(__file__).resolve().parent / "locale_values" / f"{code}.list.json"
    if not p.is_file():
        logger.warning(
            "i18n : %s absent — locale %s reprend l’anglais "
            "(node tools/extract_en_list.mjs && node tools/build_locale_lists.mjs).",
            p.name,
            code,
        )
        return tuple(en.values())
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{p}: expected JSON array")
    return tuple(str(x) for x in raw)


def _key_overrides(code: str) -> dict[str, str]:
    p = Path(__file__).resolve().parent / "locale_values" / f"{code}.keys.json"
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{p}: expected JSON object")
    return {str(k): str(v) for k, v in raw.items()}


def merge_extra_locales(messages: dict[str, dict[str, str]]) -> None:
    en = messages["en"]
    keys = list(en.keys())
    values_en = list(en.values())
    n = len(keys)
    assert len(values_en) == n
    for code in ("de", "es", "it", "pt"):
        vals = list(_value_tuple(code, en))
        if len(vals) < n:
            missing = n - len(vals)
            logger.warning(
                "i18n locale %s : %d chaîne(s) manquante(s), complétées à partir de l anglais "
                "(relancez tools/build_locale_lists.mjs ou mettez à jour %s.list.json).",
                code,
                missing,
                code,
            )
            vals = vals + values_en[len(vals) :]
        elif len(vals) > n:
            logger.warning(
                "i18n locale %s : %d chaîne(s) en trop, troncature (anglais = %d entrées).",
                code,
                len(vals) - n,
                n,
            )
            vals = vals[:n]
        merged = dict(zip(keys, vals))
        if len(merged) != len(keys):
            raise RuntimeError(f"i18n {code}: duplicate key in zip")
        overrides = _key_overrides(code)
        if overrides:
            merged.update(overrides)
        messages[code] = merged
