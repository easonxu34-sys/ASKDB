from __future__ import annotations

from functools import lru_cache

import tiktoken


_ENCODINGS = {
    "tiktoken:cl100k_base": "cl100k_base",
    "tiktoken:o200k_base": "o200k_base",
}


@lru_cache(maxsize=len(_ENCODINGS))
def _encoding(tokenizer_id: str):
    try:
        encoding_name = _ENCODINGS[tokenizer_id]
    except KeyError as exc:
        raise ValueError("tokenizer id is not supported") from exc
    return tiktoken.get_encoding(encoding_name)


class TiktokenCounter:
    """Count with an explicitly selected encoding; never infer from a model name."""

    def count_tokens(self, text: str, *, tokenizer_id: str) -> int:
        return len(_encoding(tokenizer_id).encode(text, disallowed_special=()))
