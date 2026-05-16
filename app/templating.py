"""Templates Jinja2 partagés et contexte i18n (t, lang)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.i18n import translate, SUPPORTED_LOCALES

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def template_context(request: Request, **extra):
    """À fusionner dans chaque TemplateResponse : expose t(), lang, chemins pour le sélecteur de langue."""
    lang = getattr(request.state, "locale", "fr")

    def t(key: str, **fmt) -> str:
        return translate(lang, key, **fmt)

    raw_next = request.url.path
    if request.url.query:
        raw_next += "?" + request.url.query
    return {
        "request": request,
        "lang": lang,
        "t": t,
        "i18n_next": quote(raw_next, safe=""),
        "locale_choices": sorted(SUPPORTED_LOCALES),
        **extra,
    }
