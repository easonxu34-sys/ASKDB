from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any


_MAX_THREAD_WRITE_BYTES = 64 * 1024
_MAX_CHAT_REQUEST_BYTES = 512 * 1024
_MAX_BUSINESS_RULE_REQUEST_BYTES = 128 * 1024
_THREAD_MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _ThreadBodyTooLarge(Exception):
    pass


class _ChatBodyTooLarge(Exception):
    pass


class ThreadRequestBodyLimitMiddleware:
    """Bound thread mutation bodies before FastAPI buffers/parses JSON."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method")
        if method == "POST" and path == "/v1/chart-edits/interpret":
            limit = 64 * 1024
            exception = _ThreadBodyTooLarge
            error_code = "CHART_EDIT_REQUEST_TOO_LARGE"
            message = "图表编辑请求不能超过 64 KiB。"
        elif method == "POST" and path == "/v1/chat":
            limit = _MAX_CHAT_REQUEST_BYTES
            exception = _ChatBodyTooLarge
            error_code = "CHAT_REQUEST_TOO_LARGE"
            message = "聊天请求不能超过 512 KiB。"
        elif method in _THREAD_MUTATION_METHODS and (
            path == "/v1/business-rules" or path.startswith("/v1/business-rules/")
        ):
            limit = _MAX_BUSINESS_RULE_REQUEST_BYTES
            exception = _ThreadBodyTooLarge
            error_code = "BUSINESS_RULE_REQUEST_TOO_LARGE"
            message = "业务规则候选请求不能超过 128 KiB。"
        elif method in _THREAD_MUTATION_METHODS and (
            path == "/v1/threads" or path.startswith("/v1/threads/")
        ):
            limit = _MAX_THREAD_WRITE_BYTES
            exception = _ThreadBodyTooLarge
            error_code = "THREAD_REQUEST_TOO_LARGE"
            message = "会话写入请求不能超过 64 KiB。"
        else:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await self._reject(send, error_code, message)
                    return
            except ValueError:
                await self._reject(send, error_code, message)
                return

        if method == "POST" and path == "/v1/chart-edits/interpret":
            # Bound the bytes before FastAPI's JSON parser can turn a receive
            # exception into a generic 400. Keep the other endpoint paths intact.
            buffered = []
            total = 0
            while True:
                event = await receive()
                if event.get("type") == "http.request":
                    total += len(event.get("body", b""))
                    if total > limit:
                        await self._reject(send, error_code, message)
                        return
                buffered.append(event)
                if event.get("type") != "http.request" or not event.get("more_body", False):
                    break
            cursor = 0

            async def replay_receive():
                nonlocal cursor
                if cursor < len(buffered):
                    event = buffered[cursor]
                    cursor += 1
                    return event
                return await receive()

            await self.app(scope, replay_receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise exception
            return message

        try:
            await self.app(scope, limited_receive, send)
        except (_ThreadBodyTooLarge, _ChatBodyTooLarge):
            await self._reject(send, error_code, message)

    @staticmethod
    async def _reject(send, code: str, message: str) -> None:
        body = json.dumps(
            {
                "detail": {
                    "code": code,
                    "message": message,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
