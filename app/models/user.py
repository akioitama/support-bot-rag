from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    workos_user_id = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    first_name = Column(String, default="", nullable=False)
    last_name = Column(String, default="", nullable=False)
    role = Column(String, default="user", nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    messages = relationship(
        "ChatMessage",
        back_populates="user",
        cascade="all, delete-orphan",
    )
