import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from app.config import settings

SESSION_KEY = "authenticated"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def is_authenticated(request: Request) -> bool:
    return request.session.get(SESSION_KEY) is True


def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def login_user(request: Request):
    request.session[SESSION_KEY] = True


def logout_user(request: Request):
    request.session.clear()


def check_admin_password(password: str) -> bool:
    """
    Compare with the hashed password stored in the DB (settings table).
    Falls back to the plain .env value on first boot.
    """
    from app.database import SessionLocal, init_db
    from app.models.models import AppSetting

    # Ensure tables exist (handles cold start where startup hasn't fired yet)
    init_db()

    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == "admin_password_hash").first()
        if row:
            return verify_password(password, row.value)
        # First boot: compare plain text, then store hash
        if password == settings.admin_password:
            hashed = hash_password(password)
            db.add(AppSetting(key="admin_password_hash", value=hashed))
            db.commit()
            return True
        return False
    except Exception:
        # Fallback: plain text comparison if DB is unavailable
        return password == settings.admin_password
    finally:
        db.close()
