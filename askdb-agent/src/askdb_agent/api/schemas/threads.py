from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ThreadHistoryImportTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_content: str = Field(min_length=1, max_length=8192)
    assistant_content: str = Field(default="", max_length=8192)


class ThreadHistoryImportDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
    )
    chunk_hashes: list[str] = Field(min_length=1, max_length=64)
    turn_count: int = Field(ge=1, le=500)
    content_bytes: int = Field(ge=1, le=2 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_chunk_hashes(self) -> ThreadHistoryImportDescriptor:
        if any(not re.fullmatch(r"[a-f0-9]{64}", item) for item in self.chunk_hashes):
            raise ValueError("history import chunk hashes must be SHA-256 hex digests")
        return self


class ThreadCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_id: str = Field(min_length=1, max_length=128)
    creation_key: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
    )
    initial_history: list[ThreadHistoryImportTurn] = Field(
        default_factory=list,
        max_length=500,
    )
    history_import: ThreadHistoryImportDescriptor | None = None

    @model_validator(mode="after")
    def bound_history_payload(self) -> ThreadCreateInput:
        if self.history_import is not None and self.initial_history:
            raise ValueError("chunked and inline history imports cannot be combined")
        content_size = sum(
            len(turn.user_content.encode("utf-8"))
            + len(turn.assistant_content.encode("utf-8"))
            for turn in self.initial_history
        )
        if content_size > 2 * 1024 * 1024:
            raise ValueError("legacy history exceeds the 2 MiB limit")
        return self


class ThreadHistoryImportChunkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
    )
    chunk_index: int = Field(ge=0, le=63)
    turns: list[ThreadHistoryImportTurn] = Field(min_length=1, max_length=500)


class ThreadDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impact_version: str = Field(min_length=32, max_length=64)
    confirmed: bool
    idempotency_key: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
    )


class ThreadTurnOutput(BaseModel):
    turn_id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
