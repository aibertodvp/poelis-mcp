"""Bounded inputs for the reviewed Poelis SDK 1.3.1 operations."""

from datetime import date
from typing import Annotated, Literal, Self, override
from unicodedata import category

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)


def validate_identifier(value: str) -> str:
    if not value.strip() or any(category(character) == "Cc" for character in value):
        raise ValueError("Identifier must be nonblank and contain no control characters")
    return value


def validate_date(value: str) -> str:
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Date must be a real calendar date in YYYY-MM-DD format")
    return value


_JSON_VALUE = TypeAdapter(JsonValue, config=ConfigDict(allow_inf_nan=False))


def validate_encoded_json(value: str) -> str:
    _JSON_VALUE.validate_json(value)
    return value


type Identifier = Annotated[
    str,
    Field(min_length=1, max_length=256, description="Opaque Poelis identifier; not a URL path."),
    AfterValidator(validate_identifier),
]
type ShortText = Annotated[str, Field(max_length=256)]
type Name = Annotated[str, Field(min_length=1, max_length=256)]
type Query = Annotated[str, Field(max_length=4096, description="SDK free-text search filter.")]
type Text = Annotated[str, Field(max_length=65536)]
type AuditText = Annotated[str, Field(max_length=4096)]
type PageSize = Annotated[int, Field(ge=1, le=200, description="Maximum results in this page.")]
type VersionNumber = Annotated[int, Field(ge=1, le=2147483647)]
type EncodedJSON = Annotated[
    str,
    Field(max_length=65536, description="JSON-encoded value string, as required by the SDK."),
    AfterValidator(validate_encoded_json),
]
type DateValue = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Real calendar date in YYYY-MM-DD format."),
    AfterValidator(validate_date),
]


class InputModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )


class SDKResult(BaseModel):
    """Actual SDK JSON result; retain upstream field names and nullable values."""

    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    data: JsonValue = Field(description="Unmodified SDK result, except documented version paging.")


class Pagination(InputModel):
    limit: PageSize = 50
    offset: Annotated[int, Field(ge=0, le=2147483647, description="Zero-based result offset.")] = 0


class ListWorkspaces(Pagination):
    """List workspaces accessible to the configured SDK account."""


class GetWorkspace(InputModel):
    workspace_id: Identifier


class GetUserAccess(InputModel):
    user_id: Identifier


class ListProducts(Pagination):
    workspace_id: Identifier
    q: Query | None = None
    limit: PageSize = 100


class ListProductVersions(Pagination):
    product_id: Identifier


class SetBaseline(InputModel):
    product_id: Identifier
    version_number: VersionNumber


class ItemFilters(Pagination):
    product_id: Identifier
    q: Query | None = None
    root_only: bool | None = Field(default=None, description="Return root items when true.")
    parent_item_id: Identifier | None = Field(
        default=None, description="Return this parent and its direct children."
    )
    limit: PageSize = 100


class ListItems(ItemFilters):
    include_deleted: bool | None = Field(default=None, description="Include soft-deleted drafts.")


class GetItem(InputModel):
    item_id: Identifier


class ListVersionItems(ItemFilters):
    version_number: VersionNumber


class SearchProducts(Pagination):
    q: Query
    workspace_id: Identifier
    limit: PageSize = 20


class SearchItems(Pagination):
    product_id: Identifier
    q: Query | None = None
    parent_item_id: Identifier | None = None
    limit: PageSize = 20


class SearchProperties(Pagination):
    q: Query
    workspace_id: Identifier | None = None
    product_id: Identifier | None = None
    item_id: Identifier | None = None
    property_type: ShortText | None = Field(default=None, description="SDK property-type filter.")
    category: ShortText | None = None
    sort: ShortText | None = Field(default=None, description="SDK sort string, not an expression.")
    limit: PageSize = 20


class PropertyUpdate[T](InputModel):
    """None omits an SDK argument; it does not request null-clearing."""

    id: Identifier
    value: T | None = Field(default=None, description="New value; null means omit, not clear.")
    item_id: Identifier | None = Field(default=None, description="Move property to this item.")
    name: Name | None = None
    readable_id: Name | None = None
    position: float | None = Field(default=None, description="Finite ordering position.")
    reason: AuditText | None = Field(default=None, description="Reason for change history.")
    description: AuditText | None = Field(
        default=None, description="Description for change history."
    )

    def has_effective_update(self) -> bool:
        return any(
            value is not None
            for value in (self.value, self.item_id, self.name, self.readable_id, self.position)
        )

    @model_validator(mode="after")
    def require_effective_update(self) -> Self:
        if not self.has_effective_update():
            raise ValueError(
                "Supply an effective update; nulls and audit fields alone do not change a property"
            )
        return self


class UpdateNumericProperty(PropertyUpdate[EncodedJSON]):
    category: ShortText | None = None
    display_unit: ShortText | None = None

    @override
    def has_effective_update(self) -> bool:
        return (
            super().has_effective_update()
            or self.category is not None
            or self.display_unit is not None
        )


class UpdateMatrixProperty(UpdateNumericProperty):
    """Matrix values use SDK JSON strings, not a locally invented matrix representation."""


class UpdateTextProperty(PropertyUpdate[Text]):
    """Update text; an empty string is a value, unlike null/omission."""


class UpdateDateProperty(PropertyUpdate[DateValue]):
    """Update a date using the SDK's calendar-date string."""


class UpdateStatusProperty(PropertyUpdate[Literal["DRAFT", "UNDER_REVIEW", "DONE"]]):
    """Update a status using the SDK's enum values."""
