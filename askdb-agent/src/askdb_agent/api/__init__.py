"""HTTP boundary for the AskDB Agent service.

Exports preserve the historical ``askdb_agent.api:app`` Uvicorn target.
"""

from askdb_agent.api.app import create_app
from askdb_agent.api.schemas.chat import ChatMessage, ChatRequest

app = create_app()

__all__ = ["ChatMessage", "ChatRequest", "app", "create_app"]
