from unittest.mock import patch

from app.models.chat import ChatMessage
from app.services.ai_service import AIServiceError
from tests.conftest import auth_client, create_user


def test_chat_requires_authentication(client):
    response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 401


def test_chat_rejects_empty_message(client, db):
    user = create_user(db)
    auth_client(client, db, user)

    response = client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 422


def test_chat_rejects_missing_message(client, db):
    user = create_user(db)
    auth_client(client, db, user)

    response = client.post("/api/chat", json={})
    assert response.status_code == 422


def test_authenticated_user_can_chat(client, db):
    user = create_user(db)
    auth_client(client, db, user)

    with patch("app.routes.chat.get_ai_response", return_value="You can reset your password from the login page.") as mock_ai:
        response = client.post("/api/chat", json={"message": "How can I reset my password?"})

    assert response.status_code == 200
    assert response.json()["response"] == "You can reset your password from the login page."
    mock_ai.assert_called_once_with("How can I reset my password?")

    saved = db.query(ChatMessage).filter(ChatMessage.user_id == user.id).one()
    assert saved.message == "How can I reset my password?"
    assert saved.response == "You can reset your password from the login page."


def test_chat_when_ollama_unavailable(client, db):
    user = create_user(db)
    auth_client(client, db, user)

    with patch(
        "app.routes.chat.get_ai_response",
        side_effect=AIServiceError(
            "Local AI service is unavailable. Please make sure Ollama is running."
        ),
    ):
        response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_chat_history_is_limited_to_current_user(client, db):
    user_a = create_user(db, email="a@example.com", workos_user_id="user_a")
    user_b = create_user(db, email="b@example.com", workos_user_id="user_b")
    db.add_all(
        [
            ChatMessage(user_id=user_a.id, message="Hello from A", response="Hi A"),
            ChatMessage(user_id=user_b.id, message="Hello from B", response="Hi B"),
        ]
    )
    db.commit()

    auth_client(client, db, user_a)
    response = client.get("/api/chat/history")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["message"] == "Hello from A"
    assert body[0]["response"] == "Hi A"


def test_history_requires_authentication(client):
    response = client.get("/api/chat/history")
    assert response.status_code == 401
