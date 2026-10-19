"""Opt-in, read-only MCP → official SDK → staging verification.

POELIS_LIVE_SESSION_DIR points to a running local session. No live writes or
credential files are created; an absent opt-in makes no network requests.
"""

import os
from pathlib import Path

import pytest
from test_http_server import call, connected

from poelis_mcp.launcher import load_session


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.skipif(
    not os.environ.get("POELIS_LIVE_SESSION_DIR"),
    reason="Live staging reads require POELIS_LIVE_SESSION_DIR opt-in",
)
@pytest.mark.anyio
async def test_sdk_reads_through_authenticated_mcp() -> None:
    directory = Path(os.environ["POELIS_LIVE_SESSION_DIR"])
    connection = load_session(directory)
    token = (directory / "token").read_text().strip()
    async with connected(connection.resource_url, token, "legacy") as client:
        workspaces = await call(client, "list_workspaces", {"limit": 2})
        assert isinstance(workspaces, list)
        for row in workspaces:
            assert isinstance(row, dict) and isinstance(row["id"], str)
            workspace = await call(client, "get_workspace", {"workspace_id": row["id"]})
            assert isinstance(workspace, dict) and workspace["id"] == row["id"]
            assert workspace["name"] == row["name"]
            products = await call(client, "list_products", {"workspace_id": row["id"], "limit": 2})
            assert isinstance(products, dict) and isinstance(products["data"], list)
            for product in products["data"]:
                assert isinstance(product, dict) and isinstance(product["id"], str)
                assert product["workspaceId"] == row["id"]
                items = await call(client, "list_items", {"product_id": product["id"], "limit": 1})
                assert isinstance(items, list)
                for item in items:
                    assert isinstance(item, dict) and isinstance(item["id"], str)
                    fetched = await call(client, "get_item", {"item_id": item["id"]})
                    assert isinstance(fetched, dict) and fetched["id"] == item["id"]
                    assert fetched["productId"] == product["id"]
