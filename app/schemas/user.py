from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class UserPublic(BaseModel):
    """Current authenticated user returned by GET /api/users/me."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    workos_user_id: str
    email: str
    first_name: str
    last_name: str
    role: str


class UserAdmin(BaseModel):
    """User record returned by admin endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str
    last_name: str
    role: str
    chat_count: int = 0
    created_at: Optional[datetime] = None


class RoleUpdate(BaseModel):
    role: Literal["user", "admin"]
