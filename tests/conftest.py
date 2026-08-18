import os

# Configure the test environment before the application is imported.
os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SESSION_SECRET"] = "test-session-secret-not-for-production-use"
os.environ["WORKOS_API_KEY"] = ""
os.environ["WORKOS_CLIENT_ID"] = ""
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "llama3.2"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.chat import ChatMessage
from app.models.user import User


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db: Session):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_user(
    db: Session,
    email: str = "user@example.com",
    role: str = "user",
    workos_user_id: str = "user_normal",
    first_name: str = "Test",
    last_name: str = "User",
) -> User:
    user = User(
        workos_user_id=workos_user_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth_client(client: TestClient, db: Session, user: User) -> TestClient:
    def override_current_user():
        return db.query(User).filter(User.id == user.id).one()

    app.dependency_overrides[get_current_user] = override_current_user
    return client
