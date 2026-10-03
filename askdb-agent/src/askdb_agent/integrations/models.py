from __future__ import annotations

from langchain.chat_models import init_chat_model
from typing import Any


def build_model(settings: Any, **model_options):
    """Create the configured LangChain chat model."""
    if settings.base_url or settings.api_key:
        model_name = settings.model.removeprefix("openai:")
        return init_chat_model(
            model_name,
            model_provider="openai",
            base_url=settings.base_url,
            api_key=settings.api_key,
            **model_options,
        )
    return init_chat_model(settings.model, **model_options)
