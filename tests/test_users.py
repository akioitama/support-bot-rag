from tests.conftest import auth_client, create_user


def test_current_user_profile(client, db):
    user = create_user(db, first_name="Daniyal", last_name="Ali")
    auth_client(client, db, user)

    response = client.get("/api/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user.id
    assert body["first_name"] == "Daniyal"
    assert body["last_name"] == "Ali"
    assert body["role"] == "user"


def test_normal_user_cannot_list_users(client, db):
    user = create_user(db)
    auth_client(client, db, user)
    response = client.get("/api/admin/users")
    assert response.status_code == 403
