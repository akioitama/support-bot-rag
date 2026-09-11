from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str
    error_message: str
    chunk_count: int
    created_at: Optional[datetime] = None


class DocumentStatusOut(BaseModel):
    pending: int
    processing: int
    ready: int
    failed: int
