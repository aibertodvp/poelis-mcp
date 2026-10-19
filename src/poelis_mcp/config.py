"""Explicit operator configuration; upstream credentials are environment references only."""

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=1024)]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True, frozen=True)


def _nonblank(value: str) -> bool:
    return (
        bool(value.strip())
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _literal_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_url(value: str, *, allow_insecure: bool) -> None:
    if not _nonblank(value) or any(character.isspace() for character in value) or "\\" in value:
        raise ValueError("Invalid endpoint URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid endpoint URL") from None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or "*" in value
        or "%" in parsed.netloc
    ):
        raise ValueError("Invalid endpoint URL")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid endpoint port")
    if parsed.scheme != "https" and not (
        allow_insecure and parsed.scheme == "http" and _literal_loopback(parsed.hostname)
    ):
        raise ValueError("Endpoint requires HTTPS")


class PrincipalConfig(_ConfigModel):
    subject: Identifier
    account: Identifier

    @model_validator(mode="after")
    def validate_identifiers(self) -> Self:
        if not all(_nonblank(value) for value in (self.subject, self.account)):
            raise ValueError("Invalid principal identifier")
        return self


class AccountConfig(_ConfigModel):
    sdk_key_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=256)]
    base_url: str = "https://api-staging.poelis.com"


class Settings(_ConfigModel):
    issuer_url: str
    jwks_url: str
    resource_url: str
    accounts: dict[str, AccountConfig]
    principals: list[PrincipalConfig]
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    max_request_bytes: Annotated[int, Field(ge=1, le=8 * 1024 * 1024)] = 1024 * 1024
    max_response_bytes: Annotated[int, Field(ge=1, le=64 * 1024 * 1024)] = 8 * 1024 * 1024
    allow_insecure_loopback: bool = False
    allowed_clients: list[Identifier] = Field(default_factory=list)
    jwks_cache_seconds: Annotated[int, Field(ge=1, le=3600)] = 300

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        for url in (
            self.issuer_url,
            self.jwks_url,
            self.resource_url,
            *(account.base_url for account in self.accounts.values()),
        ):
            _validate_url(url, allow_insecure=self.allow_insecure_loopback)
        if urlsplit(self.resource_url).path != "/mcp":
            raise ValueError("Resource URL must use the /mcp path")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", self.host):
                raise ValueError("Invalid bind host") from None
        if urlsplit(self.resource_url).scheme == "http" and not _literal_loopback(self.host):
            raise ValueError("Insecure HTTP requires a literal loopback bind address")
        if any(not _nonblank(name) for name in self.accounts):
            raise ValueError("Invalid account identifier")
        subjects: set[str] = set()
        for principal in self.principals:
            if principal.subject in subjects:
                raise ValueError("Duplicate principal subject")
            if principal.account not in self.accounts:
                raise ValueError("Principal references an undefined SDK account")
            subjects.add(principal.subject)
        if any(not _nonblank(client) for client in self.allowed_clients):
            raise ValueError("Invalid allowed client identifier")
        if len(set(self.allowed_clients)) != len(self.allowed_clients):
            raise ValueError("Duplicate allowed client identifier")
        return self


def load_settings(path: Path) -> Settings:
    """Read JSON without environment overlays or exposing invalid values in errors."""
    try:
        return Settings.model_validate_json(path.read_bytes())
    except OSError, ValueError, ValidationError:
        raise ValueError("Invalid configuration") from None
