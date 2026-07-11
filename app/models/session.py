from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.constraints import Constraints


class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    role: ChatRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionState(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    messages: list[ChatMessage] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    constraints_complete: bool = False
    plan_id: str | None = None  # set once /compose has created a Plan
