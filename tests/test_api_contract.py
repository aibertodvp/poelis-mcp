"""Consumer-visible boundaries for the explicit Poelis SDK tool surface."""

import pytest
from pydantic import BaseModel, ValidationError

from poelis_mcp.operations import OPERATIONS


def validate(name: str, payload: dict[str, object]) -> BaseModel:
    return OPERATIONS[name].input_model.model_validate(payload)


@pytest.mark.parametrize("limit", [0, 201, "10", True])
def test_pagination_rejects_invalid_or_coerced_limits(limit: object) -> None:
    with pytest.raises(ValidationError):
        validate("list_workspaces", {"limit": limit})


@pytest.mark.parametrize("offset", [-1, 2147483648, "0", True])
def test_pagination_rejects_invalid_offsets(offset: object) -> None:
    with pytest.raises(ValidationError):
        validate("list_workspaces", {"offset": offset})


@pytest.mark.parametrize("identifier", ["", " ", "x\n", "x\x7f", "x" * 257])
def test_identifiers_are_bounded_and_control_free(identifier: str) -> None:
    with pytest.raises(ValidationError):
        validate("get_item", {"item_id": identifier})


@pytest.mark.parametrize("field", ["api_key", "sdk_key", "base_url", "query", "method"])
def test_unknown_fields_cannot_select_credentials_or_raw_dispatch(field: str) -> None:
    with pytest.raises(ValidationError):
        validate("get_item", {"item_id": "item", field: "private-test-secret"})


def test_validation_error_text_hides_rejected_input() -> None:
    with pytest.raises(ValidationError) as error:
        validate("get_item", {"item_id": "item", "api_key": "private-test-secret"})
    assert "private-test-secret" not in str(error.value)


@pytest.mark.parametrize("version", [0, -1, True, "1", 2147483648])
def test_versions_require_positive_graphql_integers(version: object) -> None:
    with pytest.raises(ValidationError):
        validate("set_product_baseline_version", {"product_id": "p", "version_number": version})


def test_draft_and_version_item_contracts_do_not_mix() -> None:
    with pytest.raises(ValidationError):
        validate("get_item", {"item_id": "i", "version": "baseline"})
    with pytest.raises(ValidationError):
        validate(
            "list_version_items", {"product_id": "p", "version_number": 1, "include_deleted": True}
        )
    with pytest.raises(ValidationError):
        validate("list_items", {"product_id": "p", "root_only": "true"})
    validate("list_items", {"product_id": "p", "root_only": False, "include_deleted": None})


@pytest.mark.parametrize("kind", ["numeric", "matrix", "text", "date", "status"])
@pytest.mark.parametrize("extra", [{}, {"value": None}, {"reason": "Audit only"}])
def test_property_update_requires_effective_change(kind: str, extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate(f"update_{kind}_property", {"id": "property", **extra})


@pytest.mark.parametrize("kind", ["numeric", "matrix"])
def test_numeric_and_matrix_values_are_json_strings(kind: str) -> None:
    validate(f"update_{kind}_property", {"id": "p", "value": "[[1,2],[3,4]]"})
    with pytest.raises(ValidationError):
        validate(f"update_{kind}_property", {"id": "p", "value": [[1, 2]]})
    with pytest.raises(ValidationError):
        validate(f"update_{kind}_property", {"id": "p", "value": "not JSON"})
    validate(f"update_{kind}_property", {"id": "p", "display_unit": "mm"})


@pytest.mark.parametrize("value", ["2025-02-29", "2024-2-29", "2024-02-29T00:00:00Z", ""])
def test_dates_require_real_iso_calendar_dates(value: str) -> None:
    with pytest.raises(ValidationError):
        validate("update_date_property", {"id": "p", "value": value})


def test_dates_and_status_use_actual_sdk_values() -> None:
    validate("update_date_property", {"id": "p", "value": "2024-02-29"})
    for status in ("DRAFT", "UNDER_REVIEW", "DONE"):
        validate("update_status_property", {"id": "p", "value": status})
    with pytest.raises(ValidationError):
        validate("update_status_property", {"id": "p", "value": "Under Review"})


@pytest.mark.parametrize("position", [float("inf"), float("nan"), "1", True])
def test_position_is_finite_and_not_coerced(position: object) -> None:
    with pytest.raises(ValidationError):
        validate("update_text_property", {"id": "p", "position": position})


def test_payload_text_and_search_are_bounded() -> None:
    with pytest.raises(ValidationError):
        validate("update_text_property", {"id": "p", "value": "x" * 65537})
    with pytest.raises(ValidationError):
        validate("search_properties", {"q": "x" * 4097})
    with pytest.raises(ValidationError):
        validate("update_text_property", {"id": "p", "value": "ok", "changed_via": "SDK"})


def test_search_filters_match_sdk_optional_string_shape() -> None:
    validate("search_items", {"product_id": "p"})
    validate(
        "search_properties",
        {"q": "mass", "property_type": "numeric", "sort": "name", "category": "MASS"},
    )
    with pytest.raises(ValidationError):
        validate("search_properties", {"q": "mass", "sort": {"field": "name"}})
