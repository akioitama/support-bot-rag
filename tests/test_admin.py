from tests.conftest import auth_client, create_user


def test_unauthenticated_admin_access(client):
    response = client.get("/api/admin/users")
    assert response.status_code == 401


def test_normal_user_forbidden_from_admin_endpoints(client, db):
    user = create_user(db)
    auth_client(client, db, user)

    assert client.get("/api/admin/users").status_code == 403
    assert client.get(f"/api/admin/users/{user.id}").status_code == 403
    assert (
        client.patch(f"/api/admin/users/{user.id}/role", json={"role": "admin"}).status_code
        == 403
    )


def test_admin_can_list_users(client, db):
    admin = create_user(db, email="admin@example.com", role="admin", workos_user_id="user_admin")
    create_user(db, email="member@example.com", workos_user_id="user_member")
    auth_client(client, db, admin)

    response = client.get("/api/admin/users")
    assert response.status_code == 200
    emails = [item["email"] for item in response.json()]
    assert "admin@example.com" in emails
    assert "member@example.com" in emails


def test_admin_can_get_one_user(client, db):
    admin = create_user(db, email="admin@example.com", role="admin", workos_user_id="user_admin")
    member = create_user(db, email="member@example.com", workos_user_id="user_member")
    auth_client(client, db, admin)

    response = client.get(f"/api/admin/users/{member.id}")
    assert response.status_code == 200
    assert response.json()["email"] == "member@example.com"


def test_admin_get_missing_user(client, db):
    admin = create_user(db, email="admin@example.com", role="admin", workos_user_id="user_admin")
    auth_client(client, db, admin)
    response = client.get("/api/admin/users/999")
    assert response.status_code == 404


def test_admin_can_change_role(client, db):
    admin = create_user(db, email="admin@example.com", role="admin", workos_user_id="user_admin")
    member = create_user(db, email="member@example.com", workos_user_id="user_member")
    auth_client(client, db, admin)

    response = client.patch(f"/api/admin/users/{member.id}/role", json={"role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"

    db.refresh(member)
    assert member.role == "admin"

    response = client.patch(f"/api/admin/users/{member.id}/role", json={"role": "user"})
    assert response.status_code == 200
    assert response.json()["role"] == "user"


def test_admin_rejects_invalid_role(client, db):
    admin = create_user(db, email="admin@example.com", role="admin", workos_user_id="user_admin")
    member = create_user(db, email="member@example.com", workos_user_id="user_member")
    auth_client(client, db, admin)

    response = client.patch(f"/api/admin/users/{member.id}/role", json={"role": "superadmin"})
    assert response.status_code == 422
