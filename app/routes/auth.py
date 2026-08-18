from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.roles import (
    current_admin_emails,
    login_should_set_session,
    post_login_path,
    role_for_email,
)
from app.services.workos_service import (
    WorkOSNotConfiguredError,
    authenticate_with_code,
    extract_workos_user,
    get_authorization_url,
)

router = APIRouter(tags=["auth"])

WORKOS_NOT_CONFIGURED_HTML = """
<!DOCTYPE html>
<html>
<head><title>WorkOS is not configured</title></head>
<body>
  <h1>WorkOS is not configured</h1>
  <p>This application uses WorkOS for authentication and cannot log users in until credentials are set.</p>
  <p>Add the following values to your <code>.env</code> file:</p>
  <pre>WORKOS_API_KEY=your_api_key
WORKOS_CLIENT_ID=your_client_id
WORKOS_REDIRECT_URI=http://localhost:8000/auth/callback</pre>
  <p>See README.md for WorkOS dashboard setup steps.</p>
</body>
</html>
"""


def _workos_not_configured_response() -> HTMLResponse:
    return HTMLResponse(content=WORKOS_NOT_CONFIGURED_HTML, status_code=503)


def _start_workos_login(request: Request, login_next: str | None):
    if login_next == "admin":
        request.session["login_next"] = "admin"
    else:
        request.session.pop("login_next", None)
    try:
        authorization_url = get_authorization_url(screen_hint="sign-in")
    except WorkOSNotConfiguredError:
        return _workos_not_configured_response()
    return RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/login")
def login(request: Request):
    """Normal user sign-in. After login, users land on chat."""
    return _start_workos_login(request, login_next=None)


@router.get("/auth/login-admin")
def login_admin(request: Request):
    """Admin sign-in. After login, admins land on the admin page."""
    return _start_workos_login(request, login_next="admin")


@router.get("/auth/callback")
def auth_callback(request: Request, db: Session = Depends(get_db), code: str | None = None):
    """Handle the WorkOS redirect, create/update the local user, and start a session."""
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code from WorkOS.",
        )

    try:
        auth_response = authenticate_with_code(code)
    except WorkOSNotConfiguredError:
        return _workos_not_configured_response()

    workos_user = extract_workos_user(auth_response)
    user = _upsert_local_user(db, workos_user)
    login_next = request.session.pop("login_next", None)
    frontend = settings.FRONTEND_URL.rstrip("/")
    path = post_login_path(user.role, login_next)

    if login_should_set_session(user.role, login_next):
        request.session["user_id"] = user.id
    else:
        request.session.clear()

    return RedirectResponse(url=frontend + path, status_code=status.HTTP_302_FOUND)


@router.get("/auth/logout")
def logout(request: Request, next: str | None = None):
    """Clear the application session."""
    request.session.clear()
    frontend = settings.FRONTEND_URL.rstrip("/")
    if next == "admin":
        return RedirectResponse(url=frontend + "/admin-login.html", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url=frontend + "/login.html", status_code=status.HTTP_302_FOUND)


def _upsert_local_user(db: Session, workos_user: dict) -> User:
    admin_emails = current_admin_emails()
    user = (
        db.query(User)
        .filter(User.workos_user_id == workos_user["workos_user_id"])
        .first()
    )
    if user is None:
        user = db.query(User).filter(User.email == workos_user["email"]).first()

    assigned_role = role_for_email(
        workos_user["email"],
        user.role if user is not None else "user",
        admin_emails,
    )

    if user is None:
        user = User(
            workos_user_id=workos_user["workos_user_id"],
            email=workos_user["email"],
            first_name=workos_user["first_name"],
            last_name=workos_user["last_name"],
            role=assigned_role,
        )
        db.add(user)
    else:
        user.workos_user_id = workos_user["workos_user_id"]
        user.email = workos_user["email"]
        user.first_name = workos_user["first_name"]
        user.last_name = workos_user["last_name"]
        user.role = assigned_role

    db.commit()
    db.refresh(user)
    return user
