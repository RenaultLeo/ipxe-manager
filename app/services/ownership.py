"""Filtrage des ressources par propriétaire (rôle user vs admin)."""
from __future__ import annotations

from sqlalchemy.orm import Query, Session

from app.auth import ROLE_ADMIN, SessionUser
from app.models.models import AutoConfig, BootEntry, IsoVersion, Upload


def filter_iso_versions(db: Session, user: SessionUser | None) -> Query:
    q = db.query(IsoVersion)
    if not user:
        return q.filter(IsoVersion.id < 0)
    if user.role != ROLE_ADMIN:
        q = q.filter(IsoVersion.owner_user_id == user.id)
    return q


def owned_iso_version_ids(db: Session, user: SessionUser) -> list[int]:
    return [row[0] for row in filter_iso_versions(db, user).with_entities(IsoVersion.id).all()]


def can_modify_iso_version(user: SessionUser | None, version: IsoVersion) -> bool:
    if not user or not version:
        return False
    if user.role == ROLE_ADMIN:
        return True
    return version.owner_user_id == user.id


def get_iso_version_view(db: Session, user: SessionUser, version_id: int) -> IsoVersion | None:
    """Lecture : tout utilisateur authentifié peut voir n'importe quelle version."""
    if not user:
        return None
    return db.query(IsoVersion).get(version_id)


def get_iso_version(db: Session, user: SessionUser, version_id: int) -> IsoVersion | None:
    """Écriture : propriétaire ou administrateur uniquement."""
    v = get_iso_version_view(db, user, version_id)
    if not v:
        return None
    if can_modify_iso_version(user, v):
        return v
    return None


def filter_autoconfigs(db: Session, user: SessionUser | None) -> Query:
    q = db.query(AutoConfig)
    if not user:
        return q.filter(AutoConfig.id < 0)
    if user.role != ROLE_ADMIN:
        ids = owned_iso_version_ids(db, user)
        if not ids:
            return q.filter(AutoConfig.id < 0)
        q = q.filter(AutoConfig.iso_version_id.in_(ids))
    return q


def get_autoconfig_view(db: Session, user: SessionUser, config_id: int) -> AutoConfig | None:
    cfg = db.query(AutoConfig).get(config_id)
    if not user or not cfg:
        return None
    return cfg


def get_autoconfig(db: Session, user: SessionUser, config_id: int) -> AutoConfig | None:
    cfg = get_autoconfig_view(db, user, config_id)
    if not cfg:
        return None
    v = get_iso_version_view(db, user, cfg.iso_version_id)
    if not v or not can_modify_iso_version(user, v):
        return None
    return cfg


def get_boot_entry(db: Session, user: SessionUser, entry_id: int) -> BootEntry | None:
    entry = db.query(BootEntry).get(entry_id)
    if not entry:
        return None
    v = get_iso_version_view(db, user, entry.iso_version_id)
    if not v or not can_modify_iso_version(user, v):
        return None
    return entry


def filter_uploads(db: Session, user: SessionUser | None) -> Query:
    q = db.query(Upload)
    if not user:
        return q.filter(Upload.id < 0)
    if user.role != ROLE_ADMIN:
        q = q.filter(Upload.owner_user_id == user.id)
    return q


def get_upload(db: Session, user: SessionUser, upload_id: int) -> Upload | None:
    u = db.query(Upload).get(upload_id)
    if not u:
        return None
    if user.role == ROLE_ADMIN:
        return u
    if u.owner_user_id == user.id:
        return u
    return None
