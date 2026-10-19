"""Local asymmetric JWT and deny-by-default configuration contracts."""

import asyncio
import base64
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.api_jws import PyJWS
from pydantic import JsonValue, TypeAdapter, ValidationError

from poelis_mcp import auth
from poelis_mcp.auth import JWTVerifier
from poelis_mcp.config import AccountConfig, PrincipalConfig, Settings, load_settings

_JSON = TypeAdapter[dict[str, JsonValue]](dict[str, JsonValue])


def _settings() -> Settings:
    return Settings(
        issuer_url="https://issuer.example",
        jwks_url="https://issuer.example/jwks",
        resource_url="https://mcp.example/mcp",
        accounts={"personal": AccountConfig(sdk_key_env="POELIS_TEST_SDK_KEY")},
        principals=[PrincipalConfig(subject="alice", account="personal")],
        allowed_clients=["client"],
    )


def _claims() -> dict[str, JsonValue]:
    now = int(time.time())
    return {
        "iss": "https://issuer.example",
        "aud": "https://mcp.example/mcp",
        "sub": "alice",
        "client_id": "client",
        "exp": now + 300,
        "iat": now - 1,
        "scope": "poelis:read poelis:write",
    }


def _encoded_number(value: int) -> str:
    return (
        base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8))
        .rstrip(b"=")
        .decode("ascii")
    )


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, JsonValue]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": _encoded_number(numbers.n),
        "e": _encoded_number(numbers.e),
    }


def _token(
    key: rsa.RSAPrivateKey,
    claims: dict[str, JsonValue],
    kid: str = "first",
) -> str:
    headers: dict[str, str] = {"kid": kid}
    return PyJWS().encode(_JSON.dump_json(claims), key, algorithm="RS256", headers=headers)


class _JWKS:
    def __init__(self, keys: list[JsonValue]) -> None:
        self.keys = keys
        self.requests = 0
        self.status = 200

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://issuer.example/jwks"
        assert "authorization" not in request.headers
        self.requests += 1
        return httpx.Response(
            self.status,
            content=_JSON.dump_json({"keys": self.keys}),
            headers={"content-type": "application/json"},
        )


def test_valid_token_has_only_bound_identity_and_resource() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _JWKS([_jwk(key, "first")])

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks.respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            token = _token(key, _claims())
            result = await verifier.verify_token(token)
            assert result is not None
            assert result.subject == "alice"
            assert result.client_id == "client"
            assert result.account_id == "personal"
            assert result.resource == "https://mcp.example/mcp"
            assert result.scopes == ["poelis:read", "poelis:write"]
            assert result.claims == {"iss": "https://issuer.example"}
            assert token not in repr(result)
            assert token not in result.model_dump_json()
            assert await verifier.verify_token(token) is not None
            assert jwks.requests == 1

    asyncio.run(exercise())


def test_invalid_and_malformed_claims_are_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _JWKS([_jwk(key, "first")])
    invalid: list[Mapping[str, JsonValue]] = [
        {"iss": "https://attacker.example"},
        {"aud": "https://other.example/mcp"},
        {"aud": ["https://other.example/mcp"]},
        {"aud": ["https://mcp.example/mcp", 42]},
        {"exp": int(time.time()) - 30},
        {"exp": True},
        {"exp": "9999999999"},
        {"exp": 9999999999.0},
        {"iat": True},
        {"iat": int(time.time()) + 300},
        {"nbf": int(time.time()) + 300},
        {"nbf": "123"},
        {"sub": "unknown"},
        {"sub": ""},
        {"sub": 123},
        {"client_id": "other-client"},
        {"client_id": ""},
        {"client_id": 123},
        {"client_id": "client", "azp": "other-client"},
        {"scope": ["poelis:read"]},
        {"scope": "poelis:read\npoelis:write"},
    ]

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks.respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            for update in invalid:
                claims = _claims()
                claims.update(update)
                assert await verifier.verify_token(_token(key, claims)) is None
            for missing in ("iss", "aud", "sub", "exp", "iat", "client_id", "scope"):
                claims = _claims()
                del claims[missing]
                assert await verifier.verify_token(_token(key, claims)) is None
            claims = _claims()
            del claims["client_id"]
            claims["azp"] = "client"
            claims["aud"] = ["https://other.example", "https://mcp.example/mcp"]
            assert await verifier.verify_token(_token(key, claims)) is not None

    asyncio.run(exercise())


def test_bad_signature_algorithm_and_opaque_tokens_are_rejected() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _JWKS([_jwk(key, "first")])

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks.respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            assert await verifier.verify_token(_token(attacker, _claims())) is None
            headers: dict[str, str] = {"kid": "first"}
            for algorithm in ("HS256", "none"):
                token = PyJWS().encode(
                    _JSON.dump_json(_claims()),
                    "a" * 64 if algorithm == "HS256" else "",
                    algorithm=algorithm,
                    headers=headers,
                )
                assert await verifier.verify_token(token) is None
            for token in ("opaque", "", "a.b.c", "a" * 20000):
                assert await verifier.verify_token(token) is None

    asyncio.run(exercise())


def test_key_rotation_refreshes_once_and_unknown_kids_do_not_stampede() -> None:
    first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _JWKS([_jwk(first, "first")])

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks.respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            assert await verifier.verify_token(_token(first, _claims())) is not None
            jwks.keys = [_jwk(second, "second")]
            rotated = _token(second, _claims(), "second")
            results = await asyncio.gather(*(verifier.verify_token(rotated) for _ in range(8)))
            assert all(result is not None for result in results)
            assert jwks.requests == 2
            for index in range(10):
                assert await verifier.verify_token(_token(first, _claims(), f"bad-{index}")) is None
            assert jwks.requests == 2
            assert await verifier.verify_token(_token(first, _claims())) is None

    asyncio.run(exercise())


def test_jwks_failures_and_duplicate_key_ids_fail_closed() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _JWKS([_jwk(key, "first"), _jwk(key, "first")])

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks.respond)) as http:
            assert await JWTVerifier(_settings(), http).verify_token(_token(key, _claims())) is None
            jwks.status = 503
            verifier = JWTVerifier(_settings(), http)
            for _ in range(10):
                assert await verifier.verify_token(_token(key, _claims())) is None
            assert jwks.requests == 2

    asyncio.run(exercise())


def test_cached_keys_do_not_wait_for_unknown_key_refresh() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        requests = 0

        async def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests > 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, content=_JSON.dump_json({"keys": [_jwk(key, "first")]}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            valid = _token(key, _claims())
            assert await verifier.verify_token(valid) is not None
            refresh = asyncio.create_task(verifier.verify_token(_token(key, _claims(), "unknown")))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert await asyncio.wait_for(verifier.verify_token(valid), 1) is not None
                assert not refresh.done()
            finally:
                release.set()
                assert await refresh is None

    asyncio.run(exercise())


def test_slow_failed_refresh_cooldowns_start_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Clock:
        now: float = 0.0

        def monotonic(self) -> float:
            return self.now

        def time(self) -> float:
            return time.time()

    clock = Clock()
    monkeypatch.setattr(auth, "time", clock)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests > 1:
            clock.now += 6
            return httpx.Response(503)
        return httpx.Response(200, content=_JSON.dump_json({"keys": [_jwk(key, "first")]}))

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            verifier = JWTVerifier(_settings(), http)
            valid = _token(key, _claims())
            unknown = _token(key, _claims(), "unknown")
            assert await verifier.verify_token(valid) is not None
            assert await verifier.verify_token(unknown) is None
            assert await verifier.verify_token(unknown) is None
            assert requests == 2
            assert await verifier.verify_token(valid) is not None
            clock.now = 301
            assert await verifier.verify_token(valid) is None
            assert await verifier.verify_token(valid) is None
            assert requests == 3

    asyncio.run(exercise())


def test_config_rejects_unsafe_urls_and_unknown_accounts() -> None:
    config = _JSON.validate_json(_settings().model_dump_json())
    invalid: list[Mapping[str, JsonValue]] = [
        {"issuer_url": "http://issuer.example"},
        {"jwks_url": "https://user:secret@issuer.example/jwks"},
        {"jwks_url": "https://issuer.example/jwks?secret=value"},
        {"resource_url": "https://mcp.example/other"},
        {"resource_url": "https://mcp.example/mcp#fragment"},
        {"resource_url": "http://mcp.example/mcp", "allow_insecure_loopback": True},
        {"host": "*"},
        {"port": True},
        {"jwks_cache_seconds": 0},
        {"jwks_cache_seconds": 86400},
        {"principals": [{"subject": "alice", "account": "missing"}]},
        {"principals": [{"subject": "alice", "account": "personal"}] * 2},
    ]
    for update in invalid:
        with pytest.raises(ValidationError):
            Settings.model_validate(dict(config) | dict(update))
    config.update(
        issuer_url="http://127.0.0.1:9000",
        jwks_url="http://127.0.0.1:9000/jwks",
        resource_url="http://127.0.0.1:8000/mcp",
        allow_insecure_loopback=True,
    )
    assert Settings.model_validate(config).allow_insecure_loopback


def test_load_settings_hides_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"issuer_url":"sensitive-invalid-value"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid configuration") as error:
        load_settings(path)
    assert "sensitive-invalid-value" not in str(error.value)


def test_real_http_jwks_endpoint_verifies_asymmetric_token() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    content = _JSON.dump_json({"keys": [_jwk(key, "first")]})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/jwks":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        issuer = f"http://127.0.0.1:{server.server_port}"
        config = _JSON.validate_json(_settings().model_dump_json())
        config.update(
            issuer_url=issuer,
            jwks_url=f"{issuer}/jwks",
            resource_url="http://127.0.0.1:8000/mcp",
            allow_insecure_loopback=True,
        )
        settings = Settings.model_validate(config)
        claims = _claims()
        claims.update(iss=issuer, aud=settings.resource_url)

        async def exercise() -> None:
            async with httpx.AsyncClient(trust_env=False) as http:
                result = await JWTVerifier(settings, http).verify_token(_token(key, claims))
                assert result is not None
                assert result.subject == "alice"

        try:
            asyncio.run(exercise())
        finally:
            server.shutdown()
            thread.join(timeout=5)
