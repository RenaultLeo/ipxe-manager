"""Limites Starlette pour les formulaires ``multipart/form-data``."""
from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException

from app.config import settings


def form_str(form: FormData, key: str, default: str = "") -> str:
    """Champ texte depuis un formulaire parsé."""
    val = form.get(key)
    if val is None:
        return default
    return str(val).strip()


def form_int(form: FormData, key: str, default: int = 0) -> int:
    raw = form_str(form, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def pick_upload_file(form: FormData, key: str) -> UploadFile | None:
    """Récupère un ``UploadFile`` non vide depuis un formulaire multipart."""
    item = form.get(key)
    if item is None or not isinstance(item, UploadFile):
        return None
    fn = (getattr(item, "filename", None) or "").strip()
    return item if fn else None


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


def is_multipart_request(request: Request) -> bool:
    if request.method not in ("POST", "PUT", "PATCH"):
        return False
    content_type = request.headers.get("content-type", "")
    return content_type.startswith("multipart/form-data")


async def read_multipart_form(request: Request, *, lang: str | None = None) -> FormData:
    """Parse multipart avec limites configurables (fichiers, champs, taille par partie)."""
    try:
        return await request.form(**multipart_parser_kwargs())
    except MultiPartException as exc:
        raise _multipart_http_error(exc, lang=lang) from exc


async def read_form(request: Request, *, lang: str | None = None) -> FormData:
    """Parse urlencoded ou multipart (limites projet sur multipart uniquement)."""
    if is_multipart_request(request):
        return await read_multipart_form(request, lang=lang)
    return await request.form()
