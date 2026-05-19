"""Création du compte admin initial et attribution des ISO existantes."""
from __future__ import annotations

import logging

from app.auth import ROLE_ADMIN, hash_password
from app.config import settings
from app.database import SessionLocal
from app.models.models import AppSetting, IsoVersion, Upload, User

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_USERNAME = "admin"


def bootstrap_users() -> None:
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            _assign_legacy_owners(db)
            return

        pwd_hash: str | None = None
        row = db.query(AppSetting).filter(AppSetting.key == "admin_password_hash").first()
        if row and row.value:
            pwd_hash = row.value
        else:
            plain = settings.admin_password
            if plain:
                pwd_hash = hash_password(plain)
                if not row:
                    db.add(AppSetting(key="admin_password_hash", value=pwd_hash))
                else:
                    row.value = pwd_hash

        if not pwd_hash:
            pwd_hash = hash_password("admin")
            logger.warning(
                "Aucun mot de passe admin configuré — compte « %s » créé avec mot de passe « admin » "
                "(changez-le immédiatement).",
                DEFAULT_ADMIN_USERNAME,
            )

        admin = User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=pwd_hash,
            role=ROLE_ADMIN,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        logger.info("Compte administrateur initial créé : %s", admin.username)
        _assign_legacy_owners(db, admin_id=admin.id)
    except Exception:
        logger.exception("Échec bootstrap utilisateurs")
        db.rollback()
    finally:
        db.close()


def _assign_legacy_owners(db, admin_id: int | None = None) -> None:
    if admin_id is None:
        admin = db.query(User).filter(User.role == ROLE_ADMIN).order_by(User.id).first()
        if not admin:
            return
        admin_id = admin.id

    for v in db.query(IsoVersion).filter(IsoVersion.owner_user_id.is_(None)).all():
        v.owner_user_id = admin_id
    for u in db.query(Upload).filter(Upload.owner_user_id.is_(None)).all():
        u.owner_user_id = admin_id
    db.commit()
