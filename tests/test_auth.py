from tests.conftest import create_user, auth_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_me_unauthenticated(client):
    response = client.get("/api/users/me")
    assert response.status_code == 401


def test_login_without_workos_config(client):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert "WorkOS is not configured" in response.text


def test_signup_is_removed(client):
    response = client.get("/auth/signup", follow_redirects=False)
    assert response.status_code == 404


def test_admin_login_without_workos_config(client):
    response = client.get("/auth/login-admin", follow_redirects=False)
    assert response.status_code == 503
    assert "WorkOS is not configured" in response.text
    response = client.get("/auth/callback")
    assert response.status_code == 400


def test_root_redirects_to_frontend(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("http://localhost:3000")


def test_cors_allows_frontend_origin(client):
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_me_authenticated(client, db):
    user = create_user(db)
    auth_client(client, db, user)
    response = client.get("/api/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["role"] == "user"
    assert body["workos_user_id"] == "user_normal"
