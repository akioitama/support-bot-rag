from unittest.mock import MagicMock, patch

import httpx

from app.services.ai_service import AIServiceError, get_ai_response


def test_ai_unavailable_on_connection_error():
    with patch("app.services.ai_service.httpx.post", side_effect=httpx.ConnectError("fail")):
        try:
            get_ai_response("Hello")
            assert False, "Expected AIServiceError"
        except AIServiceError as exc:
            assert "Ollama is running" in exc.message


def test_ai_timeout():
    with patch("app.services.ai_service.httpx.post", side_effect=httpx.TimeoutException("slow")):
        try:
            get_ai_response("Hello")
            assert False, "Expected AIServiceError"
        except AIServiceError as exc:
            assert "timed out" in exc.message


def test_ai_model_not_found():
    response = MagicMock()
    response.status_code = 404
    with patch("app.services.ai_service.httpx.post", return_value=response):
        try:
            get_ai_response("Hello")
            assert False, "Expected AIServiceError"
        except AIServiceError as exc:
            assert "not found" in exc.message.lower()
            assert "ollama pull" in exc.message


def test_ai_invalid_json():
    response = MagicMock()
    response.status_code = 200
    response.json.side_effect = ValueError("bad json")
    with patch("app.services.ai_service.httpx.post", return_value=response):
        try:
            get_ai_response("Hello")
            assert False, "Expected AIServiceError"
        except AIServiceError as exc:
            assert "invalid response" in exc.message


def test_ai_success_chat_message():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"message": {"content": "  Hello! How can I help you?  "}}
    with patch("app.services.ai_service.httpx.post", return_value=response):
        assert get_ai_response("Hi") == "Hello! How can I help you?"
