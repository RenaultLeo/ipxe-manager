"""Affichage des dates BDD (UTC naïf) en heure locale serveur (Europe/Paris par défaut)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def _display_tz() -> ZoneInfo:
    name = (getattr(settings, "display_timezone", None) or "Europe/Paris").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Paris")


def as_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_local_dt(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if value is None:
        return ""
    return as_utc_aware(value).astimezone(_display_tz()).strftime(fmt)
