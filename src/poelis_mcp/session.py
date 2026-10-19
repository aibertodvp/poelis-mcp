"""Temporary loopback MCP session using a personal Poelis staging SDK key.

The issuer exposes only public JWKS. Its private signing key stays in memory;
the client JWT expires within four hours. Poelis permissions belong to the SDK
key, including writes to existing accessible objects. This is not Poelis login.
"""

import argparse
import base64
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import JsonValue, TypeAdapter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from poelis_mcp.config import AccountConfig, PrincipalConfig, Settings
from poelis_mcp.http import bound_socket, running_app, socket_url
from poelis_mcp.server import create_app

_JSON = TypeAdapter(dict[str, JsonValue])


def number_base64(number: int) -> str:
    return (
        base64.urlsafe_b64encode(number.to_bytes((number.bit_length() + 7) // 8))
        .rstrip(b"=")
        .decode()
    )


@contextmanager
def local_issuer() -> Iterator[tuple[str, rsa.RSAPrivateKey]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    document: dict[str, JsonValue] = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "local",
                "n": number_base64(public.n),
                "e": number_base64(public.e),
            }
        ]
    }
    body = _JSON.dump_json(document)

    async def jwks(request: Request) -> Response:
        if (
            request.headers.get("content-length", "0") != "0"
            or "transfer-encoding" in request.headers
        ):
            return Response(status_code=400)
        return Response(body, media_type="application/json")

    with bound_socket() as listener:
        url = socket_url(listener).removesuffix("/mcp")
        with running_app(Starlette(routes=[Route("/jwks", jwks, methods=["GET"])]), listener):
            yield url, key


class Arguments(argparse.Namespace):
    session_dir: Path
    duration_seconds: int


def duration(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Duration must be an integer") from None
    if not 1 <= seconds <= 14400:
        raise argparse.ArgumentTypeError("Duration must be between 1 and 14400 seconds")
    return seconds


def private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def serve(arguments: Arguments, stop: threading.Event) -> None:
    key = os.environ.get("POELIS_SDK_KEY")
    if not key or not key.strip():
        raise ValueError("POELIS_SDK_KEY is required in the environment")
    del key
    arguments.session_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    token_path = arguments.session_dir / "token"
    try:
        with local_issuer() as (issuer_url, signing_key), bound_socket() as listener:
            resource = socket_url(listener)
            settings = Settings(
                issuer_url=issuer_url,
                jwks_url=f"{issuer_url}/jwks",
                resource_url=resource,
                allow_insecure_loopback=True,
                accounts={
                    "personal": AccountConfig(
                        sdk_key_env="POELIS_SDK_KEY", base_url="https://api-staging.poelis.com"
                    )
                },
                principals=[PrincipalConfig(subject="staging-demo", account="personal")],
                allowed_clients=["staging-demo-client"],
            )
            with running_app(create_app(settings), listener):
                now = int(time.time())
                expires_at = now + arguments.duration_seconds
                deadline = time.monotonic() + max(0.0, expires_at - time.time())
                claims: dict[str, JsonValue] = {
                    "iss": issuer_url,
                    "aud": resource,
                    "sub": "staging-demo",
                    "client_id": "staging-demo-client",
                    "scope": "poelis:read poelis:write",
                    "iat": now,
                    "exp": expires_at,
                }
                headers: dict[str, JsonValue] = {"kid": "local"}
                token = jwt.encode(claims, signing_key, algorithm="RS256", headers=headers)
                metadata: dict[str, JsonValue] = {
                    "resource_url": resource,
                    "expires_at": expires_at,
                }
                private_file(token_path, token.encode("ascii"))
                del token
                metadata_path = arguments.session_dir / "connection.json"
                temporary_path = arguments.session_dir / "connection.tmp"
                private_file(temporary_path, _JSON.dump_json(metadata))
                temporary_path.replace(metadata_path)
                if not stop.is_set():
                    print(
                        "POELIS_STAGING_READY " + _JSON.dump_json(metadata).decode("utf-8"),
                        flush=True,
                    )
                while not stop.is_set() and time.monotonic() < deadline:
                    if (arguments.session_dir / "stop").exists():
                        break
                    stop.wait(min(0.2, max(0.0, deadline - time.monotonic())))
    finally:
        token_path.unlink(missing_ok=True)
        (arguments.session_dir / "connection.json").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=duration, default=14400)
    arguments = Arguments()
    parser.parse_args(namespace=arguments)
    stop = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    logging.disable(logging.CRITICAL)
    try:
        serve(arguments, stop)
    except Exception:
        print("Poelis staging session failed; no sensitive diagnostics emitted.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
