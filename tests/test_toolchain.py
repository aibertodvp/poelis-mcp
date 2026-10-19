"""Exercise the pinned validation toolchain, not a Poelis API contract."""

from importlib.metadata import version

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError


class SampleInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    count: int
    secret: SecretStr


def test_package_is_installed() -> None:
    assert version("poelis-mcp") == "0.0.0"


def test_valid_input_and_redacted_serialization() -> None:
    model = SampleInput(count=1, secret=SecretStr("test-only-value"))
    assert model.count == 1
    assert "test-only-value" not in repr(model)
    assert "test-only-value" not in model.model_dump_json()


@pytest.mark.parametrize("count", ["1", True, 1.5])
def test_strict_validation_rejects_coercion(count: object) -> None:
    payload: dict[str, object] = {"count": count, "secret": "test-only-value"}
    with pytest.raises(ValidationError):
        SampleInput.model_validate(payload)


def test_unknown_input_is_rejected_without_echoing_values() -> None:
    payload: dict[str, object] = {
        "count": 1,
        "secret": "test-only-value",
        "unknown": "private-input",
    }
    with pytest.raises(ValidationError) as exc:
        SampleInput.model_validate(payload)
    assert "private-input" not in str(exc.value)
    assert "test-only-value" not in str(exc.value)
