from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.auth import check_admin_password, login_user, logout_user, is_authenticated

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if check_admin_password(password):
        login_user(request)
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "error": "Mot de passe incorrect"},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=302)
