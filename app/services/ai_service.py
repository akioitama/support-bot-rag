from collections.abc import Sequence

import httpx

from app.config import settings

SYSTEM_PROMPT = (
    "You are a helpful customer support assistant.\n\n"
    "Answer the user's questions clearly and concisely.\n\n"
    "Be friendly and professional.\n\n"
    "If you do not know the answer, say that you do not know rather than making up information.\n\n"
    "Do not claim that you performed an action if you did not actually perform it."
)

UNAVAILABLE_MESSAGE = (
    "Local AI service is unavailable. Please make sure Ollama is running."
)

# Ollama is stateless, so each request resends the system prompt plus recent turns.
MAX_HISTORY_TURNS = 20

# Remember llama3.2:1b if .env says llama3.2 and only the tagged model is installed.
_chat_model: str | None = None


class AIServiceError(Exception):
    """Raised when the local Ollama service cannot produce a response."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _ollama_messages(
    user_message: str,
    history: Sequence[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for prior_user, prior_assistant in (history or [])[-MAX_HISTORY_TURNS:]:
        messages.append({"role": "user", "content": prior_user})
        messages.append({"role": "assistant", "content": prior_assistant})
    messages.append({"role": "user", "content": user_message})
    return messages


def _installed_chat_model() -> str:
    """Use OLLAMA_MODEL, or a tagged install like llama3.2:1b if that is all Ollama has."""
    global _chat_model
    if _chat_model:
        return _chat_model

    wanted = settings.OLLAMA_MODEL
    try:
        data = httpx.get(
            settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags",
            timeout=10.0,
        ).json()
    except httpx.HTTPError:
        return wanted

    names = []
    if isinstance(data, dict):
        names = [m.get("name", "") for m in data.get("models", [])]
    if wanted in names:
        _chat_model = wanted
        return wanted

    tagged = [n for n in names if n.startswith(wanted + ":")]
    if tagged:
        _chat_model = tagged[0]
        return _chat_model
    return wanted


def get_ai_response(
    user_message: str,
    history: Sequence[tuple[str, str]] | None = None,
) -> str:
    """Send a message to the local Ollama model and return the assistant text."""
    return complete_chat(_ollama_messages(user_message, history))


def complete_chat(
    messages: list[dict[str, str]],
    options: dict | None = None,
) -> str:
    """POST a chat request to Ollama and return the assistant text."""
    url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    model = _installed_chat_model()
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
    }
    if options:
        payload["options"] = options

    try:
        response = httpx.post(url, json=payload, timeout=120.0)
    except httpx.ConnectError as exc:
        raise AIServiceError(UNAVAILABLE_MESSAGE) from exc
    except httpx.TimeoutException as exc:
        raise AIServiceError(
            "The local AI service timed out. Try again, or check that Ollama is running."
        ) from exc
    except httpx.HTTPError as exc:
        raise AIServiceError(UNAVAILABLE_MESSAGE) from exc

    if response.status_code == 404:
        raise AIServiceError(
            f"The Ollama model '{model}' was not found. "
            f"Run: ollama pull {model}"
        )

    if response.status_code >= 400:
        error_text = _extract_ollama_error(response)
        if "not found" in error_text.lower():
            raise AIServiceError(
                f"The Ollama model '{model}' was not found. "
                f"Run: ollama pull {model}"
            )
        raise AIServiceError(
            "The local AI service returned an error. Please try again."
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AIServiceError("The local AI service returned an invalid response.") from exc

    message = data.get("message") if isinstance(data, dict) else None
    content = None
    if isinstance(message, dict):
        content = message.get("content")
    elif isinstance(data, dict):
        content = data.get("response")

    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("The local AI service returned an empty response.")

    return content.strip()


def _extract_ollama_error(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except ValueError:
        pass
    return response.text or ""
