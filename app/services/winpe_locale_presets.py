"""Préréglages langue interface, région et clavier pour unattend.xml."""
from __future__ import annotations

import json

from app.services.winpe_locale_data import (
    DEFAULT_INPUT_LOCALE,
    KEYBOARD_VARIANT_ROWS,
    UI_LANGUAGE_ROWS,
)

DEFAULT_UI_LANGUAGE_ID = "fr-FR"
DEFAULT_REGION_ID = "fr-FR"
DEFAULT_KEYBOARD_ID = "fr-FR"


def _ui_row(lang_id: str, label: str) -> dict[str, str]:
    return {
        "id": lang_id,
        "label": label,
        "setup_ui_language": lang_id,
        "ui_language": lang_id,
        "ui_language_fallback": lang_id,
    }


def _region_row(lang_id: str, label: str) -> dict[str, str]:
    return {
        "id": lang_id,
        "label": f"Région — {label}",
        "system_locale": lang_id,
        "user_locale": lang_id,
    }


def _build_ui_languages() -> list[dict[str, str]]:
    rows = [_ui_row(lang_id, label) for lang_id, label in UI_LANGUAGE_ROWS]
    rows.sort(key=lambda r: r["label"].casefold())
    return rows


def _build_regions() -> list[dict[str, str]]:
    rows = [_region_row(lang_id, label) for lang_id, label in UI_LANGUAGE_ROWS]
    rows.sort(key=lambda r: r["label"].casefold())
    return rows


def _build_keyboards() -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    for lang_id, label in UI_LANGUAGE_ROWS:
        hex_val = DEFAULT_INPUT_LOCALE.get(lang_id)
        if not hex_val:
            hex_val = lang_id
        kb_label = f"{label} — clavier par défaut"
        if lang_id not in seen:
            out.append({"id": lang_id, "label": kb_label, "input_locale": hex_val})
            seen.add(lang_id)

    for kb_id, kb_label, hex_val in KEYBOARD_VARIANT_ROWS:
        if kb_id in seen:
            continue
        out.append({"id": kb_id, "label": kb_label, "input_locale": hex_val})
        seen.add(kb_id)

    out.sort(key=lambda r: r["label"].casefold())
    return out


UI_LANGUAGES: list[dict[str, str]] = _build_ui_languages()
REGIONS: list[dict[str, str]] = _build_regions()
KEYBOARD_LAYOUTS: list[dict[str, str]] = _build_keyboards()

LOCALE_PRESETS: list[dict[str, str]] = [
    {
        **ui,
        "system_locale": ui["id"],
        "user_locale": ui["id"],
        "input_locale": next(
            (k["input_locale"] for k in KEYBOARD_LAYOUTS if k["id"] == ui["id"]),
            DEFAULT_INPUT_LOCALE.get(ui["id"], ui["id"]),
        ),
    }
    for ui in UI_LANGUAGES
]


def ui_language_by_id(language_id: str) -> dict[str, str] | None:
    key = (language_id or "").strip()
    if not key:
        return None
    for row in UI_LANGUAGES:
        if row["id"] == key:
            return row
    return None


def region_by_id(region_id: str) -> dict[str, str] | None:
    key = (region_id or "").strip()
    if not key:
        return None
    for row in REGIONS:
        if row["id"] == key:
            return row
    return None


def keyboard_by_id(keyboard_id: str) -> dict[str, str] | None:
    key = (keyboard_id or "").strip()
    if not key:
        return None
    for row in KEYBOARD_LAYOUTS:
        if row["id"] == key:
            return row
    return None


def locale_preset_by_id(locale_id: str) -> dict[str, str] | None:
    ui = ui_language_by_id(locale_id)
    reg = region_by_id(locale_id)
    kb = keyboard_by_id(locale_id)
    if not ui or not reg:
        return None
    merged = {**ui, **reg}
    if kb:
        merged["input_locale"] = kb["input_locale"]
    else:
        merged["input_locale"] = DEFAULT_INPUT_LOCALE.get(ui["id"], ui["id"])
    return merged


def ui_languages_for_ps_embed() -> str:
    """Langues interface du wizard = catalogue language-packs sur le serveur."""
    from app.services.winpe_language_packs import ui_languages_for_deploy_embed

    langs = ui_languages_for_deploy_embed()
    return json.dumps(langs, ensure_ascii=False, indent=2)


def regions_for_ps_embed() -> str:
    return json.dumps(REGIONS, ensure_ascii=False, indent=2)


def keyboards_for_ps_embed() -> str:
    return json.dumps(KEYBOARD_LAYOUTS, ensure_ascii=False, indent=2)


def locale_presets_for_ps_embed() -> str:
    return ui_languages_for_ps_embed()


def default_ui_language_id() -> str:
    from app.services.winpe_language_packs import (
        default_deploy_ui_language_id,
        load_catalog,
    )

    if load_catalog():
        return default_deploy_ui_language_id()
    return DEFAULT_UI_LANGUAGE_ID


def default_region_id() -> str:
    return DEFAULT_REGION_ID


def default_keyboard_id() -> str:
    return DEFAULT_KEYBOARD_ID


def default_locale_id() -> str:
    return DEFAULT_UI_LANGUAGE_ID
