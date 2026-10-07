from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from domain.memory_payload import normalize_payload

MemoryKind = Literal['expression', 'display', 'analysis_steps', 'default_filter', 'metric_definition', 'analysis_recipe']


class PersonalMemoryError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=1200)
    scenario: str = Field(default='default', min_length=1, max_length=160)
    source_id: str | None = Field(default=None, max_length=128)
    expires_at: datetime | None = None
    payload: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_content(self):
        if len(json.dumps(self.payload, ensure_ascii=False)) > 6000:
            raise ValueError('memory payload too large')
        text = self.content + json.dumps(self.payload, ensure_ascii=False)
        if re.search(r'(?i)(sk-[a-z0-9]{12,}|password\s*[:=]|api[_ -]?key\s*[:=]|SELECT\s+.+\s+FROM|INSERT\s+INTO|```)', text):
            raise ValueError('cannot save credentials, code or SQL')
        allowed = {'language', 'address', 'organization', 'unit', 'chart_type', 'steps', 'filters', 'metric', 'time_rule', 'binding', 'trigger', 'display', 'references'}
        if set(self.payload) - allowed:
            raise ValueError('unsupported memory fields')
        self.payload = normalize_payload(self.kind, self.payload)
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError('expiry requires timezone')
        return self

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MemoryAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['save', 'update', 'forget', 'clear', 'clarify', 'none']
    memories: list[MemoryInput] = Field(default_factory=list, max_length=8)
    targets: list[str] = Field(default_factory=list, max_length=8)
    clarification: str = Field(default='', max_length=500)
    query: str = Field(default='', max_length=8192)
    needs_successful_analysis: bool = False


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    expected_revision: int = Field(ge=0)


class MemoryEdit(MemoryInput):
    expected_version: int = Field(ge=1)


class ConfirmationResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=16, max_length=128)
    choice_id: str = Field(min_length=1, max_length=128)
