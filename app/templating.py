"""Templates Jinja2 partagés et contexte i18n (t, lang)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.auth import get_session_user, is_admin
from app.services.ownership import can_modify_iso_version
from app.i18n import translate, SUPPORTED_LOCALES
from app.config import settings

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
    current_user = get_session_user(request)

    def can_modify_iso(version) -> bool:
        return can_modify_iso_version(current_user, version)

    return {
        "request": request,
        "lang": lang,
        "t": t,
        "i18n_next": quote(raw_next, safe=""),
        "locale_choices": sorted(SUPPORTED_LOCALES),
        "iso_public_http_url": settings.iso_public_http_url,
        "current_user": current_user,
        "is_admin": is_admin(request),
        "can_modify_iso": can_modify_iso,
        **extra,
    }
