from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse

from app.auth import authenticate_user, get_session_user, is_authenticated, login_user, logout_user
from app.i18n import translate
from app.templating import templates, template_context

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", template_context(request, error=None))


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(...),
):
    user = authenticate_user(username, password)
    if user:
        login_user(request, user.id, user.username, user.role)
        return RedirectResponse("/", status_code=302)
    lang = getattr(request.state, "locale", "fr")
    err = translate(lang, "auth.bad_credentials")
    return templates.TemplateResponse(
        "auth/login.html",
        template_context(request, error=err),
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=302)
