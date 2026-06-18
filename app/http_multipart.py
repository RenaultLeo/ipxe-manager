"""Limites Starlette pour les formulaires ``multipart/form-data``."""
from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.datastructures import FormData
from starlette.formparsers import MultiPartException

from app.config import settings

# Parse manuel dans le handler (garde-fous disque ou éviter preload + Form/File FastAPI).
MULTIPART_MANUAL_PARSE_PATHS = frozenset({"/isos/upload"})


def uses_manual_multipart_parse(path: str) -> bool:
    """True si la route parse ``request.form()`` elle-même (pas le middleware)."""
    if path in MULTIPART_MANUAL_PARSE_PATHS:
        return True
    # Uploads boot : conflit connu preload middleware ↔ dépendances FastAPI Form/File.
    if path.startswith("/boot-files/") and (
        path.endswith("/upload") or path.endswith("/replace-wim")
    ):
        return True
    return False


def multipart_parser_kwargs() -> dict[str, int]:
    """Arguments ``request.form()`` alignés sur la config projet."""
    return {
        "max_files": settings.multipart_max_files,
        "max_fields": settings.multipart_max_fields,
        "max_part_size": settings.multipart_max_part_size,
    }


def _multipart_http_error(exc: MultiPartException, *, lang: str | None) -> HTTPException:
    from app.i18n import translate

    msg = str(exc).lower()
    if lang:
        if "too many files" in msg:
            return HTTPException(
                413,
                detail=translate(lang, "iso.upload.too_many_files"),
            )
        if "too many fields" in msg:
            return HTTPException(
                413,
                detail=translate(lang, "iso.upload.too_many_fields"),
            )
        if "maximum size" in msg or "max_part_size" in msg:
            return HTTPException(
                413,
                detail=translate(lang, "iso.upload.part_too_large"),
            )
    return HTTPException(400, detail=str(exc))


async def read_multipart_form(request: Request, *, lang: str | None = None) -> FormData:
    """Parse multipart avec limites configurables (fichiers, champs, taille par partie)."""
    try:
        return await request.form(**multipart_parser_kwargs())
    except MultiPartException as exc:
        raise _multipart_http_error(exc, lang=lang) from exc


async def preload_multipart_form(request: Request) -> None:
    """
    Met en cache le formulaire multipart avant les dépendances FastAPI ``Form`` / ``File``.
    Starlette ne parse qu'une fois ; les appels suivants réutilisent le cache.
    """
    if not _is_multipart_request(request):
        return
    if uses_manual_multipart_parse(request.url.path):
        return
    await request.form(**multipart_parser_kwargs())


def _is_multipart_request(request: Request) -> bool:
    if request.method not in ("POST", "PUT", "PATCH"):
        return False
    content_type = request.headers.get("content-type", "")
    return content_type.startswith("multipart/form-data")
