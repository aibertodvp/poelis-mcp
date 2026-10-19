"""Pinned-issuer JWT resource-server authentication and explicit local grants."""

import asyncio
import base64
import binascii
import time
from typing import Annotated, Literal

import httpx2 as httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.api_jws import PyJWS
from jwt.exceptions import PyJWTError
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from poelis_mcp.config import Settings

_MAX_TOKEN_BYTES = 16 * 1024
_MAX_JWKS_BYTES = 256 * 1024
_UNKNOWN_KEY_COOLDOWN = 5.0
_FETCH_FAILURE_COOLDOWN = 1.0
Nonempty = Annotated[str, Field(min_length=1, max_length=1024)]


class AuthorizedToken(AccessToken):
    """Internal SDK identity; never returned as a model-visible tool result."""

    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    token: str = Field(exclude=True, repr=False)
    claims: dict[str, JsonValue] | None = None
    account_id: str


class _JWTHeader(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)
    alg: Literal["RS256"]
    kid: Nonempty
    crit: list[str] = Field(default_factory=list)
    b64: bool = True


class _JWTClaims(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)
    iss: Nonempty
    aud: Nonempty | list[Nonempty]
    sub: Nonempty
    exp: int
    iat: int
    nbf: int | None = None
    client_id: Nonempty | None = None
    azp: Nonempty | None = None
    scope: str


class _JWK(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)
    kty: str
    kid: Nonempty | None = None
    alg: str | None = None
    use: str | None = None
    key_ops: list[str] | None = None
    n: str | None = None
    e: str | None = None


class _JWKS(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)
    keys: Annotated[list[_JWK], Field(min_length=1, max_length=64)]


def _base64url(value: str) -> bytes:
    if not value or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in value
    ):
        raise ValueError("Invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _identity(value: str) -> bool:
    return (
        value == value.strip()
        and bool(value)
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _rsa_keys(document: _JWKS) -> dict[str, rsa.RSAPublicKey]:
    result: dict[str, rsa.RSAPublicKey] = {}
    seen: set[str] = set()
    for key in document.keys:
        if key.kid is not None:
            if key.kid in seen:
                raise ValueError("Ambiguous signing key")
            seen.add(key.kid)
        if (
            key.kty != "RSA"
            or key.kid is None
            or key.alg not in (None, "RS256")
            or key.use not in (None, "sig")
            or (key.key_ops is not None and "verify" not in key.key_ops)
        ):
            continue
        if key.n is None or key.e is None or len(key.n) > 1366 or len(key.e) > 8:
            raise ValueError("Invalid RSA signing key")
        modulus = int.from_bytes(_base64url(key.n))
        exponent = int.from_bytes(_base64url(key.e))
        if not 2048 <= modulus.bit_length() <= 8192 or not 3 <= exponent <= 0xFFFFFFFF:
            raise ValueError("Invalid RSA signing key size")
        result[key.kid] = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    if not result:
        raise ValueError("No usable signing keys")
    return result


class JWTVerifier:
    """Verify RS256 JWTs against one configured HTTP JWKS, never token-supplied URLs.

    A lock coalesces concurrent fetches. Unknown key IDs get one refresh per
    cooldown window, including failures; stale keys are never used after cache
    expiry. Operators must rotate to a new kid and overlap keys during rollout.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._principals = {principal.subject: principal for principal in settings.principals}
        self._allowed_clients = frozenset(settings.allowed_clients)
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._expires = 0.0
        self._last_attempt = float("-inf")
        self._last_unknown_refresh = float("-inf")
        self._lock = asyncio.Lock()
        self._jws = PyJWS(algorithms=["RS256"])

    async def _refresh(self) -> bool:
        try:
            async with asyncio.timeout(10):
                async with self._http.stream(
                    "GET",
                    self._settings.jwks_url,
                    headers={"accept": "application/json"},
                    follow_redirects=False,
                    timeout=5.0,
                ) as response:
                    if response.status_code != 200:
                        return False
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > _MAX_JWKS_BYTES:
                            return False
                        content.extend(chunk)
            keys = _rsa_keys(_JWKS.model_validate_json(content))
        except httpx.HTTPError, TimeoutError, ValueError, binascii.Error:
            return False
        finally:
            # A slow attempt must not consume its own failure cooldown.
            self._last_attempt = time.monotonic()
        self._keys = keys
        self._expires = time.monotonic() + self._settings.jwks_cache_seconds
        return True

    async def _key(self, kid: str) -> rsa.RSAPublicKey | None:
        if time.monotonic() < self._expires:
            cached = self._keys.get(kid)
            if cached is not None:
                return cached
        async with self._lock:
            now = time.monotonic()
            if now < self._expires:
                if kid in self._keys:
                    return self._keys[kid]
                if now - self._last_unknown_refresh < _UNKNOWN_KEY_COOLDOWN:
                    return None
                try:
                    if not await self._refresh():
                        return None
                finally:
                    self._last_unknown_refresh = time.monotonic()
            else:
                if now - self._last_attempt < _FETCH_FAILURE_COOLDOWN:
                    return None
                if not await self._refresh():
                    return None
                if kid not in self._keys:
                    self._last_unknown_refresh = time.monotonic()
            return self._keys.get(kid)

    async def verify_token(self, token: str) -> AuthorizedToken | None:
        if not token or len(token) > _MAX_TOKEN_BYTES:
            return None
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            header = _JWTHeader.model_validate_json(_base64url(parts[0]))
            if header.crit or not header.b64 or not _identity(header.kid):
                return None
            key = await self._key(header.kid)
            if key is None:
                return None
            # PyJWT's generic payload return is narrowed at this single boundary.
            payload: object = self._jws.decode(token, key, algorithms=["RS256"])
            if not isinstance(payload, bytes):
                return None
            claims = _JWTClaims.model_validate_json(payload)
            now = time.time()
            if (
                claims.iss != self._settings.issuer_url
                or claims.exp <= now
                or claims.iat > now
                or claims.exp <= claims.iat
                or (claims.nbf is not None and claims.nbf > now)
                or ("nbf" in claims.model_fields_set and claims.nbf is None)
                or not _identity(claims.sub)
            ):
                return None
            audiences = [claims.aud] if isinstance(claims.aud, str) else claims.aud
            if self._settings.resource_url not in audiences:
                return None
            for name, value in (("client_id", claims.client_id), ("azp", claims.azp)):
                if name in claims.model_fields_set and (value is None or not _identity(value)):
                    return None
            if (
                claims.client_id is not None
                and claims.azp is not None
                and claims.client_id != claims.azp
            ):
                return None
            client_id = claims.client_id if claims.client_id is not None else claims.azp
            if client_id is None or (
                self._allowed_clients and client_id not in self._allowed_clients
            ):
                return None
            # RFC 6749 scope-token excludes controls, quote and backslash.
            if any(
                not (
                    character == " "
                    or ord(character) == 33
                    or 35 <= ord(character) <= 91
                    or 93 <= ord(character) <= 126
                )
                for character in claims.scope
            ):
                return None
            policy = self._principals.get(claims.sub)
            if policy is None:
                return None
            return AuthorizedToken(
                token=token,
                client_id=client_id,
                scopes=claims.scope.split(),
                expires_at=claims.exp,
                resource=self._settings.resource_url,
                subject=claims.sub,
                claims={"iss": self._settings.issuer_url},
                account_id=policy.account,
            )
        except PyJWTError, ValidationError, ValueError, binascii.Error:
            return None
