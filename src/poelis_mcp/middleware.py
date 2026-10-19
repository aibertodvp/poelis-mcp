"""Redact SDK validation diagnostics without parsing or bypassing authentication."""

from mcp_types import ErrorData, JSONRPCError
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = TypeAdapter(list[tuple[bytes, bytes]])


class RedactProtocolErrors:
    """Keep JSON-RPC error codes/IDs, never reflect validation inputs to clients."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type: object = scope["type"]
        path: object = scope.get("path")
        if scope_type != "http" or path != "/mcp":
            await self.app(scope, receive, send)
            return
        redacting = False
        body = bytearray()
        oversized = False
        headers: list[tuple[bytes, bytes]] = []

        async def filtered_send(message: Message) -> None:
            nonlocal redacting, oversized, headers
            message_type: object = message["type"]
            if message_type == "http.response.start":
                raw_headers: object = message.get("headers", [])
                headers = _HEADERS.validate_python(raw_headers)
                status: object = message["status"]
                redacting = status == 400 and any(
                    name.lower() == b"content-type" and value.startswith(b"application/json")
                    for name, value in headers
                )
                if redacting:
                    return
            if not redacting or message_type != "http.response.body":
                await send(message)
                return
            chunk: object = message.get("body", b"")
            if isinstance(chunk, bytes) and not oversized:
                if len(body) + len(chunk) <= 65536:
                    body.extend(chunk)
                else:
                    body.clear()
                    oversized = True
            if message.get("more_body", False):
                return
            code = -32600
            request_id: str | int | None = None
            redact_error = True
            if not oversized:
                try:
                    original = JSONRPCError.model_validate_json(body)
                    code, request_id = original.error.code, original.id
                    # Only the legacy SDK's diagnostic strings reflect raw input.
                    # Other errors carry protocol negotiation data clients need.
                    redact_error = original.error.message.startswith(
                        ("Validation error:", "Parse error:")
                    )
                except ValidationError:
                    pass
            content: bytes | str
            if redact_error:
                error = JSONRPCError(
                    jsonrpc="2.0",
                    id=request_id,
                    error=ErrorData(code=code, message="Invalid MCP request."),
                )
                content = error.model_dump_json()
            else:
                content = bytes(body)
            response = Response(content, status_code=400, media_type="application/json")
            response.raw_headers.extend(
                (name, value)
                for name, value in headers
                if name.lower() not in {b"content-type", b"content-length"}
            )
            await response(scope, receive, send)

        await self.app(scope, receive, filtered_send)
