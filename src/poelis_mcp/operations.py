"""Explicit Poelis SDK allowlist; no raw queries, methods or URL dispatch."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from poelis_mcp import models as m


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    scope: Literal["poelis:read", "poelis:write"]
    destructive: bool
    idempotent: bool


OPERATIONS: dict[str, Operation] = {
    "list_workspaces": Operation(
        name="list_workspaces",
        description="List accessible workspaces with limit/offset paging.",
        input_model=m.ListWorkspaces,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "get_workspace": Operation(
        name="get_workspace",
        description="Read a workspace through the official SDK; Poelis enforces native access.",
        input_model=m.GetWorkspace,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "get_user_accessible_resources": Operation(
        name="get_user_accessible_resources",
        description="Read user access information as authorized by Poelis for this SDK account.",
        input_model=m.GetUserAccess,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "list_products": Operation(
        name="list_products",
        description="List accessible products in a workspace, optionally filtering by free text.",
        input_model=m.ListProducts,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "list_product_versions": Operation(
        name="list_product_versions",
        description=(
            "List product versions. The SDK fetches all versions; "
            "this server applies limit/offset locally."
        ),
        input_model=m.ListProductVersions,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "set_product_baseline_version": Operation(
        name="set_product_baseline_version",
        description=(
            "Replace the product baseline with an existing version. No automatic retry or rollback."
        ),
        input_model=m.SetBaseline,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
    "list_items": Operation(
        name="list_items",
        description="List draft items; optionally filter text, roots, parent and deleted drafts.",
        input_model=m.ListItems,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "get_item": Operation(
        name="get_item",
        description="Read a current draft item by ID, not a version snapshot.",
        input_model=m.GetItem,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "list_version_items": Operation(
        name="list_version_items",
        description="List a numbered version's frozen items; parent filters use draft-scoped IDs.",
        input_model=m.ListVersionItems,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "search_products": Operation(
        name="search_products",
        description="Search products in a workspace using the SDK free-text filter.",
        input_model=m.SearchProducts,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "search_items": Operation(
        name="search_items",
        description="Search draft product items, optionally filtered by parent and free text.",
        input_model=m.SearchItems,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "search_properties": Operation(
        name="search_properties",
        description="Search properties by text, scope, type, category and sort; no raw queries.",
        input_model=m.SearchProperties,
        output_model=m.SDKResult,
        scope="poelis:read",
        destructive=False,
        idempotent=True,
    ),
    "update_numeric_property": Operation(
        name="update_numeric_property",
        description=(
            "Update numeric JSON-encoded value and/or metadata. "
            "Null omits; no automatic retry or rollback."
        ),
        input_model=m.UpdateNumericProperty,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
    "update_matrix_property": Operation(
        name="update_matrix_property",
        description=(
            "Update matrix JSON-encoded value and/or metadata. "
            "Null omits; no automatic retry or rollback."
        ),
        input_model=m.UpdateMatrixProperty,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
    "update_text_property": Operation(
        name="update_text_property",
        description=(
            "Update text value and/or metadata. Empty text is a value; null omits. "
            "No automatic retry or rollback."
        ),
        input_model=m.UpdateTextProperty,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
    "update_date_property": Operation(
        name="update_date_property",
        description=(
            "Update a real YYYY-MM-DD date and/or metadata. "
            "Null omits; no automatic retry or rollback."
        ),
        input_model=m.UpdateDateProperty,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
    "update_status_property": Operation(
        name="update_status_property",
        description=(
            "Update status to DRAFT, UNDER_REVIEW or DONE and/or metadata. "
            "Null omits; no automatic retry or rollback."
        ),
        input_model=m.UpdateStatusProperty,
        output_model=m.SDKResult,
        scope="poelis:write",
        destructive=True,
        idempotent=False,
    ),
}
