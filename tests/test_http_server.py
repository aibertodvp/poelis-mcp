"""Official MCP client over real HTTP; Poelis GraphQL and issuer are local fixtures."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx2 as httpx
import pytest
from http_support import LocalSDK, upstream_server
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import JsonValue, TypeAdapter

from poelis_mcp.config import AccountConfig, PrincipalConfig, Settings
from poelis_mcp.http import bound_socket, running_app, socket_url
from poelis_mcp.models import SDKResult
from poelis_mcp.server import create_app

_JSON = TypeAdapter(dict[str, JsonValue])


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def configuration(upstream: LocalSDK, resource: str) -> Settings:
    return Settings(
        issuer_url=upstream.url,
        jwks_url=f"{upstream.url}/jwks",
        resource_url=resource,
        allow_insecure_loopback=True,
        accounts={
            "a": AccountConfig(sdk_key_env="POELIS_TEST_KEY_A", base_url=upstream.url),
            "b": AccountConfig(sdk_key_env="POELIS_TEST_KEY_B", base_url=upstream.url),
        },
        principals=[
            PrincipalConfig(subject="alice", account="a"),
            PrincipalConfig(subject="bob", account="b"),
        ],
    )


@asynccontextmanager
async def connected(resource: str, token: str, mode: str) -> AsyncIterator[Client]:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=10, trust_env=False
    ) as http:
        async with Client(
            streamable_http_client(resource, http_client=http), mode=mode, cache=None
        ) as client:
            yield client


async def call(client: Client, name: str, arguments: Mapping[str, JsonValue]) -> JsonValue:
    payload: dict[str, JsonValue] = dict(arguments)
    result = await client.call_tool(name, payload)
    assert not result.is_error, result.model_dump_json()
    wire = _JSON.validate_json(result.model_dump_json())
    content = wire.get("structured_content", wire.get("structuredContent"))
    assert isinstance(content, dict)
    assert "fixture-sdk-key" not in result.model_dump_json()
    return SDKResult.model_validate(content).data


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_sdk_reads_and_mutations_over_mcp(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POELIS_TEST_KEY_A", "fixture-sdk-key-a")
    monkeypatch.setenv("POELIS_TEST_KEY_B", "fixture-sdk-key-b")
    with upstream_server() as upstream, bound_socket() as listener:
        resource = socket_url(listener)
        with running_app(create_app(configuration(upstream, resource)), listener):
            async with connected(resource, upstream.token(resource), mode) as client:
                catalog = await client.list_tools()
                names = {tool.name for tool in catalog.tools}
                assert len(names) == 17
                assert (
                    not {"create_product", "delete_product", "upload_file", "download_file"} & names
                )
                workspaces = await call(client, "list_workspaces", {})
                assert isinstance(workspaces, list) and workspaces[0] == {
                    "id": "workspace",
                    "orgId": "org",
                    "name": "Workspace A",
                    "readableId": "workspace",
                }
                workspace = await call(client, "get_workspace", {"workspace_id": "workspace"})
                assert isinstance(workspace, dict) and workspace["name"] == "Workspace A"
                products = await call(client, "list_products", {"workspace_id": "workspace"})
                assert isinstance(products, dict) and isinstance(products["data"], list)
                assert (
                    isinstance(products["data"][0], dict)
                    and products["data"][0]["name"] == "Product A"
                )
                access = await call(client, "get_user_accessible_resources", {"user_id": "alice"})
                assert isinstance(access, dict) and isinstance(access["workspaces"], list)
                versions = await call(
                    client,
                    "list_product_versions",
                    {"product_id": "product", "offset": 1, "limit": 1},
                )
                assert isinstance(versions, dict) and isinstance(versions["data"], list)
                assert len(versions["data"]) == 1
                baseline = await call(
                    client,
                    "set_product_baseline_version",
                    {"product_id": "product", "version_number": 2},
                )
                assert isinstance(baseline, dict) and baseline["baselineVersionNumber"] == 2
                items = await call(client, "list_items", {"product_id": "product"})
                assert (
                    isinstance(items, list)
                    and isinstance(items[0], dict)
                    and items[0]["id"] == "item"
                )
                item = await call(client, "get_item", {"item_id": "item"})
                assert isinstance(item, dict) and item["productId"] == "product"
                snapshot = await call(
                    client, "list_version_items", {"product_id": "product", "version_number": 2}
                )
                assert (
                    isinstance(snapshot, list)
                    and isinstance(snapshot[0], dict)
                    and snapshot[0]["name"] == "Snapshot 2"
                )
                for name, arguments in (
                    ("search_products", {"workspace_id": "workspace", "q": "Product"}),
                    ("search_items", {"product_id": "product", "q": "Item"}),
                    ("search_properties", {"item_id": "item", "q": ""}),
                ):
                    found = await call(client, name, arguments)
                    assert isinstance(found, dict) and isinstance(found["hits"], list)
                    assert found["hits"]
                for kind, value in (
                    ("numeric", "3.5"),
                    ("matrix", "[[1,2],[3,4]]"),
                    ("text", "Updated"),
                    ("date", "2026-10-20"),
                    ("status", "DONE"),
                ):
                    changed = await call(
                        client, f"update_{kind}_property", {"id": kind, "value": value}
                    )
                    assert isinstance(changed, dict) and changed["value"] == value
                    readback = await call(
                        client, "search_properties", {"item_id": "item", "q": kind}
                    )
                    assert isinstance(readback, dict) and isinstance(readback["hits"], list)
                    assert (
                        isinstance(readback["hits"][0], dict)
                        and readback["hits"][0]["value"] == value
                    )
                outside: dict[str, JsonValue] = {"item_id": "outside"}
                denied = await client.call_tool("get_item", outside)
                assert denied.is_error
                assert "upstream-sensitive-detail" not in denied.model_dump_json()


@pytest.mark.anyio
async def test_subject_binding_scope_and_native_denials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POELIS_TEST_KEY_A", "fixture-sdk-key-a")
    monkeypatch.setenv("POELIS_TEST_KEY_B", "fixture-sdk-key-b")
    update: dict[str, JsonValue] = {"id": "text", "value": "Forbidden"}
    empty: dict[str, JsonValue] = {}
    with upstream_server() as upstream, bound_socket() as listener:
        resource = socket_url(listener)
        with running_app(create_app(configuration(upstream, resource)), listener):
            for subject, expected in (("alice", "Workspace A"), ("bob", "Workspace B")):
                async with connected(
                    resource,
                    upstream.token(resource, subject=subject, scopes="poelis:read"),
                    "legacy",
                ) as client:
                    catalog = await client.list_tools()
                    assert len(catalog.tools) == 11
                    workspace = await call(client, "get_workspace", {"workspace_id": "workspace"})
                    assert isinstance(workspace, dict) and workspace["name"] == expected
                    before = len(upstream.calls)
                    denied = await client.call_tool("update_text_property", update)
                    assert denied.is_error and len(upstream.calls) == before
            async with connected(
                resource, upstream.token(resource, subject="bob"), "2026-07-28"
            ) as client:
                denied = await client.call_tool("update_text_property", update)
                assert (
                    denied.is_error
                )  # Poelis fixture enforces Bob's viewer role, not local grants.
                assert upstream.values["fixture-sdk-key-b"]["text"] == "Original"
            async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
                for token in (
                    None,
                    "invalid",
                    upstream.token("https://other.invalid/mcp"),
                    upstream.token(resource, subject="unknown"),
                ):
                    headers = {} if token is None else {"Authorization": "Bearer " + token}
                    response = await http.post(resource, json=empty, headers=headers)
                    assert response.status_code == 401
                for header, value, status in (
                    ("Origin", "https://evil.invalid", 403),
                    ("Host", "evil.invalid", 421),
                ):
                    response = await http.post(
                        resource,
                        json=empty,
                        headers={
                            "Authorization": "Bearer " + upstream.token(resource),
                            header: value,
                        },
                    )
                    assert response.status_code == status


@pytest.mark.anyio
async def test_malformed_legacy_envelopes_do_not_echo_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POELIS_TEST_KEY_A", "fixture-sdk-key-a")
    monkeypatch.setenv("POELIS_TEST_KEY_B", "fixture-sdk-key-b")
    with upstream_server() as upstream, bound_socket() as listener:
        resource = socket_url(listener)
        with running_app(create_app(configuration(upstream, resource)), listener):
            async with httpx.AsyncClient(trust_env=False) as http:
                headers = {
                    "Authorization": "Bearer " + upstream.token(resource),
                    "Accept": "application/json",
                    "MCP-Protocol-Version": "2025-11-25",
                }
                marker = "sensitive-envelope-value"
                payload: dict[str, JsonValue] = {"jsonrpc": marker, "id": 1, "method": "initialize"}
                response = await http.post(
                    resource,
                    headers=headers,
                    json=payload,
                )
                assert response.status_code == 400
                assert marker not in response.text
                envelope = _JSON.validate_json(response.content)
                assert isinstance(envelope["error"], dict) and envelope["error"]["code"] == -32602
                response = await http.post(
                    resource,
                    headers={**headers, "Content-Type": "application/json"},
                    content=b'{"secret":"sensitive-envelope-value" invalid}',
                )
                assert response.status_code == 400 and marker not in response.text
                envelope = _JSON.validate_json(response.content)
                assert isinstance(envelope["error"], dict) and envelope["error"]["code"] == -32700


@pytest.mark.anyio
async def test_redaction_preserves_protocol_negotiation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

    monkeypatch.setenv("POELIS_TEST_KEY_A", "fixture-sdk-key-a")
    monkeypatch.setenv("POELIS_TEST_KEY_B", "fixture-sdk-key-b")
    version = "2099-01-01"
    payload: dict[str, JsonValue] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                PROTOCOL_VERSION_META_KEY: version,
                CLIENT_CAPABILITIES_META_KEY: {},
            }
        },
    }
    with upstream_server() as upstream, bound_socket() as listener:
        resource = socket_url(listener)
        with running_app(create_app(configuration(upstream, resource)), listener):
            async with httpx.AsyncClient(trust_env=False) as http:
                response = await http.post(
                    resource,
                    json=payload,
                    headers={
                        "Authorization": "Bearer " + upstream.token(resource),
                        "Accept": "application/json",
                        "MCP-Protocol-Version": version,
                        "Mcp-Method": "tools/list",
                    },
                )
                assert response.status_code == 400
                error = _JSON.validate_json(response.content)["error"]
                assert isinstance(error, dict) and error["code"] == -32022
                metadata = error["data"]
                assert isinstance(metadata, dict)
                assert metadata["requested"] == version
                assert metadata["supported"] == ["2026-07-28"]
