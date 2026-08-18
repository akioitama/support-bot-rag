import os

from dotenv import load_dotenv

from app.config import ENV_FILE, TESTING


def parse_admin_emails(raw: str) -> set[str]:
    return {part.strip().lower() for part in (raw or "").split(",") if part.strip()}


def current_admin_emails() -> set[str]:
    """Read ADMIN_EMAILS from .env on each login so edits apply without a stale process."""
    if not TESTING:
        load_dotenv(ENV_FILE, override=True)
    return parse_admin_emails(os.getenv("ADMIN_EMAILS", ""))


def role_for_email(email: str, current_role: str | None, admin_emails: set[str]) -> str:
    if (email or "").strip().lower() in admin_emails:
        return "admin"
    return current_role or "user"


def post_login_path(role: str, login_next: str | None) -> str:
    """Where to send the browser after WorkOS. Wrong portal does not open the other app."""
    if login_next == "admin":
        if role == "admin":
            return "/admin.html"
        return "/admin-login.html?error=not_admin"
    if role == "admin":
        return "/login.html?error=admin_account"
    return "/chat.html"


def login_should_set_session(role: str, login_next: str | None) -> bool:
    if login_next == "admin":
        return role == "admin"
    return role != "admin"
