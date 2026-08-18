from app.services.roles import (
    login_should_set_session,
    parse_admin_emails,
    post_login_path,
    role_for_email,
)


def test_parse_admin_emails():
    assert parse_admin_emails("a@x.com, B@Y.com") == {"a@x.com", "b@y.com"}
    assert parse_admin_emails(" daniyal.ali@ignicube.com") == {"daniyal.ali@ignicube.com"}
    assert parse_admin_emails("") == set()


def test_role_for_email_promotes_listed_admin():
    assert role_for_email("boss@x.com", "user", {"boss@x.com"}) == "admin"


def test_role_for_email_keeps_existing_admin():
    assert role_for_email("other@x.com", "admin", set()) == "admin"


def test_role_for_email_defaults_to_user():
    assert role_for_email("member@x.com", None, set()) == "user"


def test_user_login_lands_on_chat():
    assert post_login_path("user", None) == "/chat.html"
    assert login_should_set_session("user", None) is True


def test_admin_login_lands_on_admin():
    assert post_login_path("admin", "admin") == "/admin.html"
    assert login_should_set_session("admin", "admin") is True


def test_user_cannot_use_admin_login():
    assert post_login_path("user", "admin") == "/admin-login.html?error=not_admin"
    assert login_should_set_session("user", "admin") is False


def test_admin_cannot_use_user_login():
    assert post_login_path("admin", None) == "/login.html?error=admin_account"
    assert login_should_set_session("admin", None) is False
