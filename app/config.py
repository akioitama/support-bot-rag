import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
TESTING = os.getenv("TESTING") == "1"

if not TESTING:
    # Terminals sometimes inherit empty WORKOS_* variables. Force .env values.
    load_dotenv(ENV_FILE, override=True)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=None if TESTING else str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    WORKOS_API_KEY: str = ""
    WORKOS_CLIENT_ID: str = ""
    WORKOS_REDIRECT_URI: str = "http://localhost:8000/auth/callback"

    DATABASE_URL: str = "sqlite:///./app.db"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"

    # RAG: split PDFs into pieces, then find the closest pieces for a question.
    CHUNK_SIZE: int = 1200
    CHUNK_OVERLAP: int = 200
    RAG_MIN_CHUNK_CHARS: int = 200
    RAG_TOP_K: int = 8
    RAG_CANDIDATE_K: int = 16
    RAG_MIN_SCORE: float = 0.30
    RAG_SCORE_MARGIN: float = 0.08
    RAG_REJECT_SCORE: float = 0.40
    RAG_NEIGHBOR_WINDOW: int = 1
    RAG_PROMPT_K: int = 5

    FRONTEND_URL: str = "http://localhost:3000"

    # Comma-separated emails that always receive admin rights after WorkOS login.
    ADMIN_EMAILS: str = ""

    # Used to sign the session cookie. Generated at startup if left empty.
    SESSION_SECRET: str = ""


settings = Settings()

# Sessions still work locally if SESSION_SECRET is not set, but they
# reset every time the process restarts.
if not settings.SESSION_SECRET:
    settings.SESSION_SECRET = secrets.token_hex(32)
