"""Real pinned SDK resource execution against a disposable local GraphQL boundary."""

import asyncio
import logging
import threading
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import httpx
import pytest
from pydantic import JsonValue, SecretStr, TypeAdapter

from poelis_mcp.sdk import SDKAdapter, SDKError, _BoundedTransport

_JSON = TypeAdapter[dict[str, JsonValue]](dict[str, JsonValue])
_KEY = "disposable-sdk-test-key"
_PRODUCT: dict[str, JsonValue] = {
    "id": "p",
    "name": "Engine",
    "workspaceId": "w",
    "readableId": "ENG",
    "baselineVersionNumber": 2,
    "reviewers": [],
}
_ITEM: dict[str, JsonValue] = {
    "id": "i",
    "name": "Blade",
    "productId": "p",
    "parentId": None,
    "draftItemId": "draft-i",
    "position": 1.5,
}
_PROPERTY: dict[str, JsonValue] = {
    "id": "r",
    "itemId": "i",
    "readableId": "MASS",
    "name": "Mass",
    "value": "[12,13]",
    "parsedValue": [12, 13],
    "category": "MASS",
    "displayUnit": "kg",
    "deleted": False,
}


@dataclass
class GraphQLBoundary:
    payload: dict[str, JsonValue] = field(default_factory=dict)
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    raw: bytes | None = None
    content_length: bool = True
    chunk_delay: float = 0
    requests: list[dict[str, JsonValue]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)
    encodings: list[str | None] = field(default_factory=list)
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: int = 0
    peak: int = 0
    url: str = ""
    workspaces_by_key: dict[str, list[JsonValue]] | None = None


@contextmanager
def graphql_boundary() -> Iterator[GraphQLBoundary]:
    backend = GraphQLBoundary()

    class Handler(BaseHTTPRequestHandler):
        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            request = _JSON.validate_json(self.rfile.read(int(self.headers["Content-Length"])))
            with backend.lock:
                backend.requests.append(request)
                backend.paths.append(self.path)
                backend.authorizations.append(self.headers.get("Authorization"))
                backend.encodings.append(self.headers.get("Accept-Encoding"))
                backend.active += 1
                backend.peak = max(backend.peak, backend.active)
            backend.started.set()
            try:
                if backend.release is not None:
                    backend.release.wait(timeout=5)
                payload = backend.payload
                status = backend.status
                if backend.workspaces_by_key is not None:
                    authorization = self.headers.get("Authorization", "")
                    visible = backend.workspaces_by_key.get(authorization)
                    if visible is None:
                        status = 401
                        payload = {"error": "unknown SDK key"}
                    else:
                        payload = {"data": {"workspaces": visible}}
                body = backend.raw if backend.raw is not None else _JSON.dump_json(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if backend.content_length:
                    self.send_header("Content-Length", str(len(body)))
                for key, value in backend.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if backend.chunk_delay:
                    for offset in range(0, len(body), 32):
                        self.wfile.write(body[offset : offset + 32])
                        self.wfile.flush()
                        time.sleep(backend.chunk_delay)
                else:
                    self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                pass
            finally:
                with backend.lock:
                    backend.active -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    backend.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield backend
    finally:
        if backend.release is not None:
            backend.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def adapter(backend: GraphQLBoundary, *, cap: int = 8192) -> SDKAdapter:
    return SDKAdapter(SecretStr(_KEY), base_url=backend.url, max_response_bytes=cap)


@dataclass(frozen=True)
class ReadCase:
    tool: str
    arguments: dict[str, JsonValue]
    operation: str
    response_field: str
    response: JsonValue
    variables: dict[str, JsonValue]


_READ_CASES = (
    ReadCase(
        "list_workspaces",
        {"limit": 2, "offset": 1},
        "SdkListWorkspaces",
        "workspaces",
        [{"id": "w", "name": "Workspace", "orgId": "o", "readableId": "WS"}],
        {"limit": 2, "offset": 1},
    ),
    ReadCase(
        "get_workspace",
        {"workspace_id": "w"},
        "SdkWorkspace",
        "workspace",
        {"id": "w", "name": "Workspace"},
        {"id": "w"},
    ),
    ReadCase(
        "get_user_accessible_resources",
        {"user_id": "u"},
        "SdkUserAccessibleResources",
        "userAccessibleResources",
        [{"id": "w", "name": "Workspace", "role": "VIEWER", "products": []}],
        {"userId": "u"},
    ),
    ReadCase(
        "list_products",
        {"workspace_id": "w", "q": "Engine", "limit": 2, "offset": 3},
        "SdkListProducts",
        "products",
        [_PRODUCT],
        {"ws": "w", "filter": {"q": "Engine"}, "limit": 2, "offset": 3},
    ),
    ReadCase(
        "list_items",
        {
            "product_id": "p",
            "q": "Blade",
            "root_only": False,
            "parent_item_id": "parent",
            "include_deleted": False,
            "limit": 2,
            "offset": 3,
        },
        "SdkListItems",
        "items",
        [_ITEM],
        {
            "pid": "p",
            "filter": {
                "q": "Blade",
                "rootOnly": False,
                "parentItemId": "parent",
                "includeDeleted": False,
            },
            "limit": 2,
            "offset": 3,
        },
    ),
    ReadCase("get_item", {"item_id": "i"}, "SdkItem", "item", _ITEM, {"id": "i"}),
    ReadCase(
        "list_version_items",
        {"product_id": "p", "version_number": 2, "root_only": True, "limit": 2, "offset": 3},
        "SdkListVersionItems",
        "sdkItems",
        [_ITEM],
        {
            "pid": "p",
            "version": {"productId": "p", "versionNumber": 2},
            "filter": {"rootOnly": True},
            "limit": 2,
            "offset": 3,
        },
    ),
    ReadCase(
        "search_products",
        {"workspace_id": "w", "q": "Engine"},
        "SdkSearchProducts",
        "products",
        [_PRODUCT],
        {"ws": "w", "filter": {"q": "Engine"}, "limit": 20, "offset": 0},
    ),
    ReadCase(
        "search_items",
        {"product_id": "p", "parent_item_id": "i"},
        "SdkSearchItems",
        "items",
        [_ITEM],
        {"pid": "p", "filter": {"parentItemId": "i"}, "limit": 20, "offset": 0},
    ),
    ReadCase(
        "search_properties",
        {
            "q": "Mass",
            "workspace_id": "w",
            "product_id": "p",
            "item_id": "i",
            "property_type": "NUMERIC",
            "category": "MASS",
            "sort": "name",
            "limit": 2,
            "offset": 3,
        },
        "SdkSearchProperties",
        "searchProperties",
        {
            "query": "Mass",
            "hits": [_PROPERTY],
            "total": 8,
            "limit": 2,
            "offset": 3,
            "processingTimeMs": 4,
        },
        {
            "q": "Mass",
            "ws": "w",
            "pid": "p",
            "iid": "i",
            "ptype": "NUMERIC",
            "cat": "MASS",
            "sort": "name",
            "limit": 2,
            "offset": 3,
        },
    ),
)


@pytest.mark.parametrize("case", _READ_CASES, ids=[case.tool for case in _READ_CASES])
def test_real_sdk_read_documents_and_result_parsing(case: ReadCase) -> None:
    with graphql_boundary() as backend:
        backend.payload = {"data": {case.response_field: case.response}}

        async def run() -> JsonValue:
            client = adapter(backend)
            try:
                return (await client.execute(case.tool, case.arguments)).data
            finally:
                await client.aclose()

        result = asyncio.run(run())
        assert len(backend.requests) == 1
        document = backend.requests[0]["query"]
        assert isinstance(document, str) and f"query {case.operation}(" in document
        assert backend.requests[0]["variables"] == case.variables
        assert backend.paths == ["/v1/graphql"]
        assert backend.authorizations == [f"Bearer {_KEY}"]
        assert backend.encodings == ["identity"]
        if case.tool == "list_products":
            assert isinstance(result, dict)
            assert result["limit"] == 2 and result["offset"] == 3
            products = result["data"]
            assert isinstance(products, list) and isinstance(products[0], dict)
            assert products[0]["baselineVersionNumber"] == 2
            assert products[0]["reviewers"] == []
        elif case.tool == "get_user_accessible_resources":
            assert isinstance(result, dict)
            workspaces = result["workspaces"]
            assert isinstance(workspaces, list) and isinstance(workspaces[0], dict)
            assert workspaces[0]["role"] == "VIEWER"
        elif case.tool.startswith("search_"):
            assert isinstance(result, dict)
            assert result["hits"] == (
                [_PROPERTY] if case.tool == "search_properties" else case.response
            )
            assert result["total"] == (8 if case.tool == "search_properties" else None)
            if case.tool == "search_properties":
                assert result["processing_time_ms"] == 4
        else:
            assert result == case.response


@pytest.mark.parametrize(
    "offset,expected,next_page", [(1, [2, 3], True), (3, [4], False), (9, [], False)]
)
def test_version_pagination_slices_backend_full_list_once(
    offset: int,
    expected: list[int],
    next_page: bool,
) -> None:
    versions: list[JsonValue] = [
        {
            "productId": "p",
            "versionNumber": n,
            "title": f"Release {n}",
            "createdAt": "2026-01-01T00:00:00Z",
        }
        for n in range(1, 5)
    ]
    with graphql_boundary() as backend:
        backend.payload = {"data": {"productVersions": versions}}

        async def run() -> JsonValue:
            client = adapter(backend)
            try:
                return (
                    await client.execute(
                        "list_product_versions",
                        {
                            "product_id": "p",
                            "limit": 2,
                            "offset": offset,
                        },
                    )
                ).data
            finally:
                await client.aclose()

        result = asyncio.run(run())
        assert isinstance(result, dict) and isinstance(result["data"], list)
        assert [row["versionNumber"] for row in result["data"] if isinstance(row, dict)] == expected
        assert result["offset"] == offset and result["limit"] == 2
        assert result["total_count"] == 4 and result["has_next_page"] is next_page
        assert len(backend.requests) == 1
        assert backend.requests[0]["variables"] == {"pid": "p"}


@pytest.mark.parametrize(
    "kind,value",
    [
        ("numeric", "[12,13]"),
        ("matrix", "[[1,2],[3,4]]"),
        ("text", "Updated"),
        ("date", "2028-02-29"),
        ("status", "UNDER_REVIEW"),
    ],
)
def test_property_mutations_use_sdk_variables_and_preserve_native_values(
    kind: str,
    value: str,
) -> None:
    with graphql_boundary() as backend:
        field_name = f"update{kind.title()}Property"
        returned: dict[str, JsonValue] = {
            **_PROPERTY,
            "value": "Under Review" if kind == "status" else value,
        }
        backend.payload = {"data": {field_name: returned}}
        arguments: dict[str, JsonValue] = {
            "id": "r",
            "value": value,
            "name": "Mass",
            "item_id": "i",
            "position": 2.5,
            "readable_id": "MASS",
            "reason": "Reviewed",
            "description": "Audit note",
        }
        expected: dict[str, JsonValue] = {
            "id": "r",
            "value": value,
            "name": "Mass",
            "itemId": "i",
            "position": 2.5,
            "readableId": "MASS",
            "reason": "Reviewed",
            "description": "Audit note",
        }
        if kind in {"numeric", "matrix"}:
            arguments.update(category="MASS", display_unit="kg")
            expected.update(category="MASS", displayUnit="kg")

        async def run() -> None:
            client = adapter(backend)
            try:
                result = await client.execute(f"update_{kind}_property", arguments)
                assert result.data == returned
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1
        assert backend.requests[0]["variables"] == expected
        document = backend.requests[0]["query"]
        assert isinstance(document, str)
        assert f"mutation SdkUpdate{kind.title()}Property(" in document
        assert "changedVia" not in document


def test_baseline_mutation_and_null_omission_are_sdk_native() -> None:
    with graphql_boundary() as backend:

        async def run() -> None:
            client = adapter(backend)
            try:
                backend.payload = {"data": {"setProductBaselineVersion": _PRODUCT}}
                result = await client.execute(
                    "set_product_baseline_version",
                    {
                        "product_id": "p",
                        "version_number": 2,
                    },
                )
                assert isinstance(result.data, dict)
                assert result.data["baselineVersionNumber"] == 2
                assert backend.requests[-1]["variables"] == {"productId": "p", "versionNumber": 2}
                backend.payload = {"data": {"updateTextProperty": _PROPERTY}}
                await client.execute(
                    "update_text_property", {"id": "r", "name": "New", "value": None}
                )
                assert backend.requests[-1]["variables"] == {"id": "r", "name": "New"}
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 2


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "unauthorized"),
        (403, "forbidden"),
        (429, "rate_limit_exceeded"),
        (500, "upstream_error"),
    ],
)
def test_http_errors_redacted_and_mutations_never_retried(status: int, code: str) -> None:
    with graphql_boundary() as backend:
        backend.status = status
        backend.headers = {"Retry-After": "0"}
        backend.payload = {"error": f"private upstream body {_KEY}"}

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError) as error:
                    await client.execute("update_text_property", {"id": "r", "value": "new"})
                assert error.value.code == code
                rendered = "".join(traceback.format_exception(error.value))
                assert _KEY not in rendered and "private upstream body" not in rendered
                assert error.value.__context__ is None
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1


@pytest.mark.parametrize(
    "payload,code",
    [
        (
            {"errors": [{"message": "private upstream body", "extensions": {"code": "forbidden"}}]},
            "forbidden",
        ),
        (
            {"errors": [{"message": "private upstream body", "extensions": {"code": "not_found"}}]},
            "not_found",
        ),
        ({"errors": [{"message": "private upstream body"}]}, "upstream_error"),
        ({"data": {"updateTextProperty": None}}, "upstream_error"),
    ],
)
def test_real_sdk_graphql_failures_do_not_disclose_bodies(
    payload: dict[str, JsonValue],
    code: str,
) -> None:
    with graphql_boundary() as backend:
        backend.payload = payload

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError) as error:
                    await client.execute("update_text_property", {"id": "r", "value": "new"})
                assert error.value.code == code
                assert "private upstream body" not in "".join(
                    traceback.format_exception(error.value)
                )
                assert error.value.__context__ is None
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1


@pytest.mark.parametrize("content_length", [True, False])
def test_streamed_response_cap_rejects_oversized_success(content_length: bool) -> None:
    with graphql_boundary() as backend:
        backend.content_length = content_length
        backend.payload = {"data": {"workspaces": [{"id": "w", "name": "x" * 5000}]}}

        async def run() -> None:
            client = adapter(backend, cap=512)
            try:
                with pytest.raises(SDKError, match="response_too_large"):
                    await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "identity,gzip"])
def test_compressed_responses_rejected_before_decompression(encoding: str) -> None:
    with graphql_boundary() as backend:
        backend.headers = {"Content-Encoding": encoding}
        backend.raw = b"not-even-a-valid-compressed-stream"

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError, match="unsupported_content_encoding"):
                    await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())


def test_redirects_never_forward_native_key() -> None:
    with graphql_boundary() as source, graphql_boundary() as destination:
        source.status = 307
        source.headers = {"Location": destination.url + "/v1/graphql"}

        async def run() -> None:
            client = adapter(source)
            try:
                with pytest.raises(SDKError, match="upstream_redirect"):
                    await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(source.requests) == 1 and destination.requests == []


def test_proxy_environment_is_not_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    with graphql_boundary() as backend, graphql_boundary() as proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(name, proxy.url)
        monkeypatch.setenv("NO_PROXY", "")
        backend.payload = {"data": {"workspaces": []}}

        async def run() -> None:
            client = adapter(backend)
            try:
                assert (await client.execute("list_workspaces", {})).data == []
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1 and proxy.requests == []


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("not_a_tool", {}),
        ("list_workspaces", {"limit": 201}),
        ("get_item", {"item_id": "i", "sdk_key": "other-key"}),
        ("update_text_property", {"id": "r"}),
        ("update_date_property", {"id": "r", "value": "2026-02-30"}),
        ("update_status_property", {"id": "r", "value": "Done"}),
    ],
)
def test_bad_tool_arguments_never_contact_upstream(
    tool: str,
    arguments: Mapping[str, JsonValue],
) -> None:
    with graphql_boundary() as backend:

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError):
                    await client.execute(tool, arguments)
            finally:
                await client.aclose()

        asyncio.run(run())
        assert backend.requests == []


def test_sdk_model_validation_and_invalid_json_fail_closed() -> None:
    with graphql_boundary() as backend:

        async def run() -> None:
            client = adapter(backend)
            try:
                backend.payload = {"data": {"products": [{"id": "p"}]}}
                with pytest.raises(SDKError, match="invalid_response"):
                    await client.execute("list_products", {"workspace_id": "w"})
                backend.raw = b"{private invalid json"
                with pytest.raises(SDKError, match="invalid_response"):
                    await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())


async def wait_started(backend: GraphQLBoundary, count: int = 1) -> None:
    deadline = time.monotonic() + 3
    while len(backend.requests) < count:
        if time.monotonic() >= deadline:
            raise AssertionError("Local GraphQL request did not start")
        await asyncio.sleep(0.005)


def test_cancellation_waits_for_started_mutation_and_close_drains_workers() -> None:
    with graphql_boundary() as backend:
        backend.payload = {"data": {"updateTextProperty": _PROPERTY}}
        release = threading.Event()
        backend.release = release

        async def run() -> None:
            client = adapter(backend)
            task = asyncio.create_task(
                client.execute("update_text_property", {"id": "r", "value": "new"})
            )
            await wait_started(backend)
            task.cancel()
            await asyncio.sleep(0.03)
            task.cancel()
            close = asyncio.create_task(client.aclose())
            await asyncio.sleep(0.03)
            assert not task.done() and not close.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await close
            with pytest.raises(SDKError, match="adapter_closed"):
                await client.execute("list_workspaces", {})
            await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1


def test_worker_concurrency_is_bounded_without_blocking_event_loop() -> None:
    with graphql_boundary() as backend:
        backend.payload = {"data": {"workspaces": []}}
        release = threading.Event()
        backend.release = release

        async def run() -> None:
            client = adapter(backend)
            tasks = [asyncio.create_task(client.execute("list_workspaces", {})) for _ in range(10)]
            try:
                await wait_started(backend, 4)
                await asyncio.sleep(0.04)
                assert len(backend.requests) == 4
                release.set()
                results = await asyncio.gather(*tasks)
                assert all(result.data == [] for result in results)
            finally:
                release.set()
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 10 and backend.peak <= 4


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/other",
        "https://example.com?key=secret",
    ],
)
def test_adapter_rejects_unsafe_origins(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid Poelis origin"):
        SDKAdapter(SecretStr(_KEY), base_url=url)


def test_accounts_use_separate_native_sdk_keys() -> None:
    with graphql_boundary() as backend:
        backend.workspaces_by_key = {
            "Bearer disposable-key-a": [{"id": "workspace-a"}],
            "Bearer disposable-key-b": [{"id": "workspace-b"}],
        }

        async def run() -> None:
            first = SDKAdapter(SecretStr("disposable-key-a"), base_url=backend.url)
            second = SDKAdapter(SecretStr("disposable-key-b"), base_url=backend.url)
            try:
                a, b = await asyncio.gather(
                    first.execute("list_workspaces", {}),
                    second.execute("list_workspaces", {}),
                )
                assert a.data == [{"id": "workspace-a"}]
                assert b.data == [{"id": "workspace-b"}]
            finally:
                await first.aclose()
                await second.aclose()

        asyncio.run(run())
        assert set(backend.authorizations) == {
            "Bearer disposable-key-a",
            "Bearer disposable-key-b",
        }


def test_read_timeout_is_explicit_and_mutation_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("poelis_mcp.sdk._IO_TIMEOUT", httpx.Timeout(0.05))
    with graphql_boundary() as backend:
        backend.release = threading.Event()

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError, match="upstream_timeout"):
                    await client.execute("update_text_property", {"id": "r", "value": "new"})
            finally:
                await client.aclose()

        asyncio.run(run())
        assert len(backend.requests) == 1


def test_stream_deadline_stops_slow_drip_within_io_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("poelis_mcp.sdk._STREAM_SECONDS", 0.04)
    with graphql_boundary() as backend:
        backend.chunk_delay = 0.015
        backend.payload = {"data": {"workspaces": [{"id": "w", "name": "x" * 1000}]}}

        async def run() -> None:
            client = adapter(backend)
            try:
                with pytest.raises(SDKError, match="upstream_timeout"):
                    await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/graphql"),
        ("POST", "/v1/items"),
        ("POST", "https://example.com/v1/graphql"),
    ],
)
def test_transport_rejects_non_graphql_paths(method: str, path: str) -> None:
    with graphql_boundary() as backend:
        transport = _BoundedTransport(SecretStr(_KEY), backend.url, 1024)
        try:
            with pytest.raises(SDKError, match="invalid_request"):
                transport._request(method, path, json={"query": "not sent"})
        finally:
            transport.close()
        assert backend.requests == []


def test_resource_clients_do_not_configure_logging_or_create_tracker_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    original_handlers = tuple(root.handlers)
    original_level = root.level
    with graphql_boundary() as backend:
        backend.payload = {"data": {"workspaces": []}}

        async def run() -> None:
            client = adapter(backend)
            try:
                await client.execute("list_workspaces", {})
            finally:
                await client.aclose()

        asyncio.run(run())
        assert tuple(root.handlers) == original_handlers
        assert root.level == original_level
        assert list(tmp_path.iterdir()) == []
