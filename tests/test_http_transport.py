"""Real TCP request deadlines, including keep-alive and slow application responses."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2 as httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from poelis_mcp.http import BoundedH11Protocol, bound_socket, socket_url


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class ShortDeadlines(BoundedH11Protocol):
    header_timeout = 0.2
    body_timeout = 0.3


@asynccontextmanager
async def server() -> AsyncIterator[int]:
    async def respond(request: Request) -> Response:
        await request.body()
        if request.url.path == "/slow":
            await asyncio.sleep(0.5)
        return Response(b"ok")

    app = Starlette(routes=[Route("/{path:path}", respond, methods=["GET", "POST"])])
    with bound_socket() as listener:
        port = urlsplit(socket_url(listener)).port
        assert port is not None
        protocol: type[asyncio.Protocol] = ShortDeadlines
        runtime = uvicorn.Server(
            uvicorn.Config(
                app,
                http=protocol,
                ws="none",
                lifespan="off",
                log_level="critical",
                access_log=False,
                limit_concurrency=100,
            )
        )
        task = asyncio.create_task(runtime.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not runtime.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield port
        finally:
            runtime.should_exit = True
            await asyncio.wait_for(task, 5)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prefix",
    [
        b"",
        b"GET / HTTP/1.1\r\nHost:",
        b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\nx",
    ],
)
async def test_incomplete_requests_release_connections(prefix: bytes) -> None:
    async with server() as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(prefix)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b""
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/")
                assert response.status_code == 200 and response.content == b"ok"
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.anyio
async def test_completed_input_does_not_timeout_slow_tool_response() -> None:
    async with server() as port:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(f"http://127.0.0.1:{port}/slow", content=b"complete")
            assert response.status_code == 200 and response.content == b"ok"


@pytest.mark.anyio
async def test_partial_pipelined_request_gets_its_own_deadline() -> None:
    async with server() as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\nGET / HTTP/1.1\r\nHost:")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            assert response.startswith(b"HTTP/1.1 200")
            assert await reader.readexactly(2) == b"ok"
            assert await asyncio.wait_for(reader.read(), 2) == b""
        finally:
            writer.close()
            await writer.wait_closed()
