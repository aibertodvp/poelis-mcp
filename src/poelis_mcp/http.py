"""HTTP input deadlines before ASGI dispatch; MCP framing stays with the SDK."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar, override

import h11
import uvicorn
from starlette.applications import Starlette
from uvicorn.protocols.http.h11_impl import H11Protocol

_SEND_BODY: object = h11.SEND_BODY
_IDLE: object = h11.IDLE


class BoundedH11Protocol(H11Protocol):
    """Bound idle/partial headers and bodies without limiting completed requests."""

    header_timeout: ClassVar[float] = 5.0
    body_timeout: ClassVar[float] = 30.0
    _input_deadline: asyncio.TimerHandle | None = None
    _body_cycle: object | None = None

    def _cancel_input_deadline(self) -> None:
        if self._input_deadline is not None:
            self._input_deadline.cancel()
            self._input_deadline = None

    def _arm_input_deadline(self, delay: float) -> None:
        self._cancel_input_deadline()
        self._input_deadline = self.loop.call_later(delay, self.transport.close)

    @override
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport_type: type[asyncio.Transport] = asyncio.Transport
        if not isinstance(transport, transport_type):
            raise TypeError("HTTP requires a byte-stream transport")
        super().connection_made(transport)
        self._arm_input_deadline(self.header_timeout)

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_input_deadline()
        super().connection_lost(exc)

    @override
    def handle_events(self) -> None:
        super().handle_events()
        if self.transport.is_closing():
            self._cancel_input_deadline()
        elif self.conn.their_state is _SEND_BODY:
            if self.cycle is not self._body_cycle:
                self._body_cycle = self.cycle
                self._arm_input_deadline(self.body_timeout)
        elif self.conn.their_state is _IDLE:
            self._body_cycle = None
            if self._input_deadline is None:
                self._arm_input_deadline(self.header_timeout)
        else:
            # A complete request may legitimately wait for a long-running tool.
            self._body_cycle = None
            self._cancel_input_deadline()


@contextmanager
def bound_socket() -> Iterator[socket.socket]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        yield listener


def socket_url(listener: socket.socket) -> str:
    address: object = listener.getsockname()
    if not isinstance(address, tuple) or len(address) != 2:
        raise RuntimeError("Unexpected socket address")
    port: object = address[1]
    if not isinstance(port, int):
        raise RuntimeError("Unexpected socket port")
    return f"http://127.0.0.1:{port}/mcp"


@contextmanager
def running_app(app: Starlette, listener: socket.socket) -> Iterator[None]:
    protocol: type[asyncio.Protocol] = BoundedH11Protocol
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            http=protocol,
            ws="none",
            log_level="error",
            access_log=False,
            proxy_headers=False,
            limit_concurrency=100,
            timeout_keep_alive=5,
        )
    )

    def run() -> None:
        server.run(sockets=[listener])

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Local MCP server failed to start")
        time.sleep(0.01)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("Local MCP server failed to stop")
