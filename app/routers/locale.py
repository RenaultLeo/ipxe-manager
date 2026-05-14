"""Changement de langue (cookie) — pour tests i18n."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.i18n import LOCALE_COOKIE, resolve_lang

router = APIRouter()


def _safe_internal_path(url: str | None, fallback: str = "/") -> str:
    if not url:
        return fallback
    u = url.strip()
    if not u.startswith("/") or u.startswith("//"):
        return fallback
    return u.split("#")[0][:2048]


@router.get("/set-language")
async def set_language(request: Request, lang: str = "fr", next: str | None = None):
    locale = resolve_lang(lang)
    target = _safe_internal_path(next) if next else "/"
    r = RedirectResponse(url=target, status_code=302)
    r.set_cookie(
        LOCALE_COOKIE,
        locale,
        max_age=365 * 24 * 3600,
        path="/",
        samesite="lax",
        httponly=False,
    )
    return r
