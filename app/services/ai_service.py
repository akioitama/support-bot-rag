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


class AIServiceError(Exception):
    """Raised when the local Ollama service cannot produce a response."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def get_ai_response(user_message: str) -> str:
    """Send a message to the local Ollama model and return the assistant text."""
    url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    }

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
            f"The Ollama model '{settings.OLLAMA_MODEL}' was not found. "
            f"Run: ollama pull {settings.OLLAMA_MODEL}"
        )

    if response.status_code >= 400:
        error_text = _extract_ollama_error(response)
        if "not found" in error_text.lower():
            raise AIServiceError(
                f"The Ollama model '{settings.OLLAMA_MODEL}' was not found. "
                f"Run: ollama pull {settings.OLLAMA_MODEL}"
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
