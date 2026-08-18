from typing import Any

from fastapi import HTTPException, status

from app.config import settings


class WorkOSNotConfiguredError(Exception):
    """Raised when WorkOS credentials are missing."""


def workos_is_configured() -> bool:
    return bool(settings.WORKOS_API_KEY and settings.WORKOS_CLIENT_ID)


def get_workos_client():
    """Create a WorkOS client. The app can start without credentials."""
    if not workos_is_configured():
        raise WorkOSNotConfiguredError(
            "WorkOS is not configured. Set WORKOS_API_KEY and WORKOS_CLIENT_ID in your .env file."
        )

    from workos import WorkOSClient

    return WorkOSClient(
        api_key=settings.WORKOS_API_KEY,
        client_id=settings.WORKOS_CLIENT_ID,
    )


def get_authorization_url(screen_hint: str = "sign-in") -> str:
    client = get_workos_client()
    return client.user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=settings.WORKOS_REDIRECT_URI,
        screen_hint=screen_hint,
    )


def authenticate_with_code(code: str) -> Any:
    """Exchange a WorkOS authorization code for the authenticated user."""
    client = get_workos_client()
    try:
        return client.user_management.authenticate_with_code(code=code)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WorkOS authentication failed. Please try logging in again.",
        ) from exc


def extract_workos_user(auth_response: Any) -> dict:
    """Normalize the WorkOS user object into a simple dict."""
    user = getattr(auth_response, "user", None)
    if user is None and isinstance(auth_response, dict):
        user = auth_response.get("user")

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WorkOS did not return user information.",
        )

    def _get(name: str, default: str = "") -> str:
        if isinstance(user, dict):
            value = user.get(name, default)
        else:
            value = getattr(user, name, default)
        return value if value is not None else default

    workos_user_id = _get("id")
    email = _get("email")
    if not workos_user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WorkOS user is missing an id or email.",
        )

    return {
        "workos_user_id": workos_user_id,
        "email": email,
        "first_name": _get("first_name"),
        "last_name": _get("last_name"),
    }
