from __future__ import annotations

import json
from typing import Any


def encode_sse(event: str, payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"
