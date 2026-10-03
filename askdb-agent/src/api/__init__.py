"""HTTP boundary for the AskDB Agent service.

Exports the FastAPI app for callers that use the ``api:app`` target.
"""

from api.app import create_app
from api.schemas.chat import ChatMessage, ChatRequest

app = create_app()

__all__ = ["ChatMessage", "ChatRequest", "app", "create_app"]
