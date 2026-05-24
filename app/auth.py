"""Sessions, rôles et authentification multi-utilisateurs."""
from __future__ import annotations

from dataclasses import dataclass

import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse

from app.config import settings

ROLE_ADMIN = "admin"
ROLE_USER = "user"

SESSION_USER_ID = "user_id"
SESSION_USERNAME = "username"
SESSION_ROLE = "role"


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    role: str


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def get_session_user(request: Request) -> SessionUser | None:
    uid = request.session.get(SESSION_USER_ID)
    if uid is None:
        return None
    try:
        user_id = int(uid)
    except (TypeError, ValueError):
        return None
    username = str(request.session.get(SESSION_USERNAME) or "")
    role = str(request.session.get(SESSION_ROLE) or ROLE_USER)
    if role not in (ROLE_ADMIN, ROLE_USER):
        role = ROLE_USER
    return SessionUser(id=user_id, username=username, role=role)


def is_authenticated(request: Request) -> bool:
    return get_session_user(request) is not None


def is_admin(request: Request) -> bool:
    user = get_session_user(request)
    return user is not None and user.role == ROLE_ADMIN


def auth_redirect_login(request: Request) -> RedirectResponse | None:
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def auth_redirect_admin(request: Request) -> RedirectResponse | None:
    redir = auth_redirect_login(request)
    if redir:
        return redir
    if not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return None


def require_auth(request: Request) -> SessionUser:
    user = get_session_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def require_admin(request: Request) -> SessionUser:
    user = require_auth(request)
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin_required")
    return user


def login_user(request: Request, user_id: int, username: str, role: str) -> None:
    request.session[SESSION_USER_ID] = user_id
    request.session[SESSION_USERNAME] = username
    request.session[SESSION_ROLE] = role


def logout_user(request: Request) -> None:
    request.session.clear()


def authenticate_user(username: str, password: str) -> SessionUser | None:
    from app.database import SessionLocal, init_db
    from app.models.models import User

    init_db()
    name = (username or "").strip().lower()
    if not name or not password:
        return None

    db = SessionLocal()
    try:
        row = db.query(User).filter(User.username == name).first()
        if not row or not verify_password(password, row.password_hash):
            return None
        return SessionUser(id=row.id, username=row.username, role=row.role)
    finally:
        db.close()
