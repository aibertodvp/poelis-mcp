"""Official Poelis SDK resource clients with a bounded, single-attempt transport.

The private Transport hook is intentionally pinned to poelis-sdk 1.3.1. Queries,
variable construction and resource parsing remain the SDK's responsibility.
"""

import asyncio
import json as stdlib_json
import time
from collections.abc import Mapping
from typing import override
from urllib.parse import urlsplit

import httpx
from poelis_sdk._transport import Transport
from poelis_sdk.exceptions import HTTPError as PoelisHTTPError
from poelis_sdk.items import ItemsClient
from poelis_sdk.products import ProductsClient
from poelis_sdk.properties import PropertiesClient
from poelis_sdk.search import SearchClient
from poelis_sdk.versions import VersionsClient
from poelis_sdk.workspaces import WorkspacesClient
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr, TypeAdapter, ValidationError

from poelis_mcp.models import (
    GetItem,
    GetUserAccess,
    GetWorkspace,
    ListItems,
    ListProducts,
    ListProductVersions,
    ListVersionItems,
    ListWorkspaces,
    SDKResult,
    SearchItems,
    SearchProducts,
    SearchProperties,
    SetBaseline,
    UpdateDateProperty,
    UpdateMatrixProperty,
    UpdateNumericProperty,
    UpdateStatusProperty,
    UpdateTextProperty,
)
from poelis_mcp.operations import OPERATIONS

_JSON = TypeAdapter[JsonValue](JsonValue, config=ConfigDict(allow_inf_nan=False))
_CONCURRENCY = 4
_IO_TIMEOUT = httpx.Timeout(5.0)
_STREAM_SECONDS = 20.0
_STATUS_ERRORS = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limit_exceeded",
}
_ERROR_CODES = frozenset(
    {
        *_STATUS_ERRORS.values(),
        "upstream_error",
        "upstream_redirect",
        "upstream_timeout",
        "upstream_unavailable",
        "invalid_response",
        "response_too_large",
        "unsupported_content_encoding",
        "invalid_arguments",
        "unknown_operation",
        "adapter_closed",
    }
)


class SDKError(Exception):
    """Only curated codes cross the upstream boundary; never messages or bodies."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_CODES else "upstream_error"
        super().__init__(self.code)


def _status_error(status: int) -> str:
    if 300 <= status < 400:
        return "upstream_redirect"
    return _STATUS_ERRORS.get(status, "upstream_error")


class _BoundedTransport(Transport):
    def __init__(self, key: SecretStr, base_url: str, max_response_bytes: int) -> None:
        # Do not call Transport.__init__: its client inherits proxies, and its
        # plaintext _api_key and retry implementation are deliberately unused.
        self._key = key
        self._max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            base_url=base_url,
            trust_env=False,
            follow_redirects=False,
            timeout=_IO_TIMEOUT,
            limits=httpx.Limits(
                max_connections=_CONCURRENCY, max_keepalive_connections=_CONCURRENCY
            ),
        )

    @override
    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        if extra:
            raise SDKError("invalid_request")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {self._key.get_secret_value()}",
        }

    @override
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json: object = None,
    ) -> httpx.Response:
        if method != "POST" or path != "/v1/graphql" or params is not None:
            raise SDKError("invalid_request")
        payload = _JSON.validate_python(json)
        deadline = time.monotonic() + _STREAM_SECONDS
        with self._client.stream(method, path, headers=self._headers(), json=payload) as response:
            # Never inspect error bodies, follow redirects, or retry a mutation.
            if not 200 <= response.status_code < 300:
                raise SDKError(_status_error(response.status_code))
            encoding: object = response.headers.get("Content-Encoding", "identity")
            if not isinstance(encoding, str) or encoding.strip().lower() != "identity":
                raise SDKError("unsupported_content_encoding")
            declared_length: object = response.headers.get("Content-Length")
            if declared_length is not None:
                if (
                    not isinstance(declared_length, str)
                    or not declared_length.isascii()
                    or not declared_length.isdecimal()
                ):
                    raise SDKError("invalid_response")
                if int(declared_length) > self._max_response_bytes:
                    raise SDKError("response_too_large")
            body = bytearray()
            # iter_raw avoids decompression and yields network-sized chunks; do
            # not use a large chunk_size that can hide a slow drip until filled.
            for chunk in response.iter_raw():
                if time.monotonic() > deadline:
                    raise SDKError("upstream_timeout")
                if len(body) + len(chunk) > self._max_response_bytes:
                    raise SDKError("response_too_large")
                body.extend(chunk)
            if time.monotonic() > deadline:
                raise SDKError("upstream_timeout")
            # The SDK needs a buffered native Response. Do not retain the
            # authenticated request (and therefore its key) on that response.
            return httpx.Response(
                response.status_code,
                content=bytes(body),
                request=httpx.Request("POST", "/v1/graphql"),
            )

    def close(self) -> None:
        self._client.close()


async def _drain[T](task: asyncio.Task[T]) -> tuple[T, bool]:
    """Defer repeated caller cancellation until bounded synchronous work settles."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            return result, cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


class SDKAdapter:
    def __init__(
        self,
        sdk_key: SecretStr,
        *,
        base_url: str,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        origin = urlsplit(base_url)
        if (
            origin.scheme not in {"https", "http"}
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
            or (origin.scheme == "http" and origin.hostname not in {"127.0.0.1", "::1"})
        ):
            raise ValueError("Invalid Poelis origin")
        if max_response_bytes < 1:
            raise ValueError("Response limit must be positive")
        self._transport = _BoundedTransport(sdk_key, base_url.rstrip("/"), max_response_bytes)
        # Root PoelisClient configures logging and may initialize tracking or
        # browser helpers. Resource constructors have none of those effects.
        self._workspaces = WorkspacesClient(self._transport)
        self._products = ProductsClient(self._transport)
        self._items = ItemsClient(self._transport)
        self._versions = VersionsClient(self._transport)
        self._search = SearchClient(self._transport)
        self._properties = PropertiesClient(self._transport)
        self._slots = asyncio.Semaphore(_CONCURRENCY)
        self._workers: set[asyncio.Task[SDKResult]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def execute(self, name: str, arguments: Mapping[str, JsonValue]) -> SDKResult:
        if self._closed:
            raise SDKError("adapter_closed")
        operation = OPERATIONS.get(name)
        if operation is None:
            raise SDKError("unknown_operation")
        request: BaseModel | None = None
        try:
            request = operation.input_model.model_validate(arguments)
        except ValidationError:
            pass
        if request is None:
            raise SDKError("invalid_arguments")
        async with self._slots:
            if self._closed:
                raise SDKError("adapter_closed")
            worker = asyncio.create_task(asyncio.to_thread(self._execute_sync, request))
            self._workers.add(worker)
            try:
                result, cancelled = await _drain(worker)
            finally:
                self._workers.discard(worker)
            if cancelled:
                raise asyncio.CancelledError
            return result

    def _execute_sync(self, request: BaseModel) -> SDKResult:
        # Catch and discard exception instances before raising the public error:
        # even __context__ must not retain a body, input value, request or key.
        code: str
        try:
            raw = self._dispatch(request)
            model_type: type[BaseModel] = BaseModel
            if isinstance(raw, model_type):
                data = _JSON.validate_json(raw.model_dump_json(by_alias=True))
            else:
                data = _JSON.validate_python(raw)
            if isinstance(request, ListProductVersions):
                if not isinstance(data, dict):
                    raise SDKError("invalid_response")
                rows = data.get("data")
                if not isinstance(rows, list):
                    raise SDKError("invalid_response")
                total = len(rows)
                data["data"] = rows[request.offset : request.offset + request.limit]
                data["total_count"] = total
                data["has_next_page"] = request.offset + request.limit < total
            return SDKResult(data=data)
        except SDKError as error:
            code = error.code
        except PoelisHTTPError as error:
            code = _status_error(error.status_code)
        except httpx.TimeoutException:
            code = "upstream_timeout"
        except httpx.HTTPError:
            code = "upstream_unavailable"
        except ValidationError, stdlib_json.JSONDecodeError:
            code = "invalid_response"
        except Exception:
            # SDK GraphQL RuntimeError text includes upstream messages. Unknown
            # SDK/parser failures are closed and never become diagnostic output.
            code = "upstream_error"
        raise SDKError(code)

    def _dispatch(self, request: BaseModel) -> object:
        # Explicit object assignments narrow the SDK's untyped dictionary
        # returns; validation above and JSON validation below remain mandatory.
        result: object
        match request:
            case ListWorkspaces():
                result = self._workspaces.list(limit=request.limit, offset=request.offset)
            case GetWorkspace():
                result = self._workspaces.get(workspace_id=request.workspace_id)
            case GetUserAccess():
                result = self._workspaces.get_user_accessible_resources(user_id=request.user_id)
            case ListProducts():
                result = self._products.list_by_workspace(
                    workspace_id=request.workspace_id,
                    q=request.q,
                    limit=request.limit,
                    offset=request.offset,
                )
            case ListProductVersions():
                result = self._products.list_product_versions(
                    product_id=request.product_id,
                    limit=request.limit,
                    offset=request.offset,
                )
            case SetBaseline():
                result = self._products.set_product_baseline_version(
                    product_id=request.product_id,
                    version_number=request.version_number,
                )
            case ListItems():
                result = self._items.list_by_product(
                    product_id=request.product_id,
                    q=request.q,
                    root_only=request.root_only,
                    parent_item_id=request.parent_item_id,
                    include_deleted=request.include_deleted,
                    limit=request.limit,
                    offset=request.offset,
                )
            case GetItem():
                result = self._items.get(request.item_id)
            case ListVersionItems():
                result = self._versions.list_items(
                    product_id=request.product_id,
                    version_number=request.version_number,
                    q=request.q,
                    root_only=request.root_only,
                    parent_item_id=request.parent_item_id,
                    limit=request.limit,
                    offset=request.offset,
                )
            case SearchProducts():
                result = self._search.products(
                    q=request.q,
                    workspace_id=request.workspace_id,
                    limit=request.limit,
                    offset=request.offset,
                )
            case SearchItems():
                result = self._search.items(
                    q=request.q,
                    product_id=request.product_id,
                    parent_item_id=request.parent_item_id,
                    limit=request.limit,
                    offset=request.offset,
                )
            case SearchProperties():
                result = self._search.properties(
                    q=request.q,
                    workspace_id=request.workspace_id,
                    product_id=request.product_id,
                    item_id=request.item_id,
                    property_type=request.property_type,
                    category=request.category,
                    sort=request.sort,
                    limit=request.limit,
                    offset=request.offset,
                )
            case UpdateMatrixProperty():
                result = self._properties.update_matrix_property(
                    id=request.id,
                    value=request.value,
                    item_id=request.item_id,
                    name=request.name,
                    readable_id=request.readable_id,
                    position=request.position,
                    category=request.category,
                    display_unit=request.display_unit,
                    reason=request.reason,
                    description=request.description,
                )
            case UpdateNumericProperty():
                result = self._properties.update_numeric_property(
                    id=request.id,
                    value=request.value,
                    item_id=request.item_id,
                    name=request.name,
                    readable_id=request.readable_id,
                    position=request.position,
                    category=request.category,
                    display_unit=request.display_unit,
                    reason=request.reason,
                    description=request.description,
                )
            case UpdateTextProperty():
                result = self._properties.update_text_property(
                    id=request.id,
                    value=request.value,
                    item_id=request.item_id,
                    name=request.name,
                    readable_id=request.readable_id,
                    position=request.position,
                    reason=request.reason,
                    description=request.description,
                )
            case UpdateDateProperty():
                result = self._properties.update_date_property(
                    id=request.id,
                    value=request.value,
                    item_id=request.item_id,
                    name=request.name,
                    readable_id=request.readable_id,
                    position=request.position,
                    reason=request.reason,
                    description=request.description,
                )
            case UpdateStatusProperty():
                result = self._properties.update_status_property(
                    id=request.id,
                    value=request.value,
                    item_id=request.item_id,
                    name=request.name,
                    readable_id=request.readable_id,
                    position=request.position,
                    reason=request.reason,
                    description=request.description,
                )
            case _:
                raise SDKError("unknown_operation")
        output: object = result
        return output

    async def _close(self) -> None:
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        await asyncio.to_thread(self._transport.close)

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        outcome: tuple[None, bool] = await _drain(self._close_task)
        if outcome[1]:
            raise asyncio.CancelledError
