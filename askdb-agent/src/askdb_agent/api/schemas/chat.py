from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=128)
    data_source_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    # `messages` is the bounded compatibility protocol for pre-memory clients.
    # New clients send only `message` and let the server load prior turns.
    messages: list[ChatMessage] | None = Field(default=None, min_length=1, max_length=40)
    message: ChatMessage | None = None
    turn_id: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
    )
    expected_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_protocol(self) -> ChatRequest:
        if self.message is not None:
            if self.message.role != "user":
                raise ValueError("The current turn must be a user message.")
            if self.messages is not None or self.turn_id is None or self.expected_sequence is None:
                raise ValueError("The current-turn protocol requires turn_id and expected_sequence only.")
        elif self.messages is not None:
            if not any(message.role == "user" for message in self.messages):
                raise ValueError("At least one user message is required.")
            if self.messages[-1].role != "user":
                raise ValueError("The final legacy message must be the current user question.")
            # Bound decoded message text independently of the per-field limits.
            # The ASGI middleware separately caps the encoded HTTP request body.
            if sum(len(message.content.encode("utf-8")) for message in self.messages) > 64 * 1024:
                raise ValueError("Legacy conversation text exceeds the 64 KiB content limit.")
            if self.turn_id is not None or self.expected_sequence is not None:
                raise ValueError("Legacy messages cannot include current-turn idempotency fields.")
        else:
            raise ValueError("Either current message or legacy messages are required.")
        return self
