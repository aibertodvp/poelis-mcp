"""Local-only GraphQL/JWKS fixtures; never live Poelis."""

import re
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import JsonValue, TypeAdapter

from poelis_mcp.session import number_base64

_JSON = TypeAdapter(dict[str, JsonValue])


class LocalSDK:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = self.key.public_key().public_numbers()
        self.jwks: dict[str, JsonValue] = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "local",
                    "alg": "RS256",
                    "use": "sig",
                    "n": number_base64(public.n),
                    "e": number_base64(public.e),
                }
            ]
        }
        self.url = ""
        self.calls: list[tuple[str, str]] = []
        self.values = {
            key: {
                "text": "Original",
                "numeric": "1",
                "matrix": "[[1]]",
                "date": "2026-01-01",
                "status": "DRAFT",
            }
            for key in ("fixture-sdk-key-a", "fixture-sdk-key-b")
        }
        self.baselines: dict[str, int] = {key: 1 for key in self.values}

    def token(
        self,
        resource: str,
        *,
        subject: str = "alice",
        scopes: str = "poelis:read poelis:write",
        audience: str | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, JsonValue] = {
            "iss": self.url,
            "aud": audience or resource,
            "sub": subject,
            "client_id": "test-client",
            "iat": now,
            "exp": now + 300,
            "scope": scopes,
        }
        headers: dict[str, str] = {"kid": "local"}
        return jwt.encode(claims, self.key, algorithm="RS256", headers=headers)

    def dispatch(self, key: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        query = payload.get("query")
        variables = payload.get("variables")
        assert isinstance(query, str) and isinstance(variables, dict)
        match = re.search(r"(?:query|mutation)\s+(\w+)", query)
        assert match is not None
        operation: str = match.group(1)
        self.calls.append((operation, key))
        suffix = "A" if key.endswith("-a") else "B"
        workspace: dict[str, JsonValue] = {
            "id": "workspace",
            "orgId": "org",
            "name": f"Workspace {suffix}",
            "readableId": "workspace",
        }
        product: dict[str, JsonValue] = {
            "id": "product",
            "name": f"Product {suffix}",
            "readableId": "product",
            "workspaceId": "workspace",
            "baselineVersionNumber": self.baselines[key],
            "description": None,
            "reviewers": [],
        }
        item: dict[str, JsonValue] = {
            "id": "item",
            "name": "Item",
            "description": None,
            "readableId": "item",
            "productId": "product",
            "parentId": None,
            "position": 1,
            "draftItemId": "item",
        }
        if "outside" in variables.values() or (query.startswith("mutation") and key.endswith("-b")):
            return {
                "errors": [
                    {"message": "upstream-sensitive-detail", "extensions": {"code": "forbidden"}}
                ]
            }
        if operation == "SdkListWorkspaces":
            return {"data": {"workspaces": [workspace]}}
        if operation == "SdkWorkspace":
            return {"data": {"workspace": workspace}}
        if operation == "SdkUserAccessibleResources":
            return {
                "data": {
                    "userAccessibleResources": [
                        {
                            "id": "workspace",
                            "name": "Workspace",
                            "readableId": "workspace",
                            "role": "EDITOR",
                            "products": [],
                        }
                    ]
                }
            }
        if operation in ("SdkListProducts", "SdkSearchProducts"):
            return {"data": {"products": [product]}}
        if operation == "SdkProductVersions":
            versions: list[JsonValue] = [
                {
                    "productId": "product",
                    "versionNumber": i,
                    "title": f"Version {i}",
                    "description": None,
                    "createdAt": "2026-01-01T00:00:00Z",
                }
                for i in (1, 2, 3)
            ]
            return {"data": {"productVersions": versions}}
        if operation == "SdkSetBaseline":
            version = variables["versionNumber"]
            assert isinstance(version, int)
            self.baselines[key] = version
            product["baselineVersionNumber"] = version
            return {"data": {"setProductBaselineVersion": product}}
        if operation in ("SdkListItems", "SdkSearchItems"):
            return {"data": {"items": [item]}}
        if operation == "SdkItem":
            return {"data": {"item": item}}
        if operation == "SdkListVersionItems":
            version_input = variables["version"]
            assert isinstance(version_input, dict)
            item["name"] = f"Snapshot {version_input['versionNumber']}"
            return {"data": {"sdkItems": [item]}}
        if operation == "SdkSearchProperties":
            q = variables["q"]
            hits: list[JsonValue] = [
                {
                    "id": kind,
                    "workspaceId": "workspace",
                    "productId": "product",
                    "itemId": "item",
                    "propertyType": kind.upper(),
                    "name": kind,
                    "category": None,
                    "value": value,
                }
                for kind, value in self.values[key].items()
                if not q or q == kind
            ]
            return {
                "data": {
                    "searchProperties": {
                        "query": q,
                        "hits": hits,
                        "total": len(hits),
                        "limit": variables["limit"],
                        "offset": variables["offset"],
                        "processingTimeMs": 0,
                    }
                }
            }
        update_types = {
            f"SdkUpdate{kind}Property": kind.lower()
            for kind in ("Numeric", "Matrix", "Text", "Date", "Status")
        }
        if operation in update_types:
            kind = update_types[operation]
            value = variables.get("value")
            if isinstance(value, str):
                self.values[key][kind] = value
            field = "update" + kind.title() + "Property"
            return {
                "data": {
                    field: {
                        "id": kind,
                        "itemId": "item",
                        "name": kind,
                        "value": self.values[key][kind],
                        "position": 1,
                        "deleted": False,
                    }
                }
            }
        raise AssertionError(f"Unexpected SDK operation: {operation}")


@contextmanager
def upstream_server() -> Iterator[LocalSDK]:
    backend = LocalSDK()

    class Handler(BaseHTTPRequestHandler):
        @override
        def log_message(self, format: str, *args: object) -> None:
            return

        def respond(self, status: int, payload: dict[str, JsonValue]) -> None:
            content = _JSON.dump_json(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            self.respond(200, backend.jwks) if self.path == "/jwks" else self.respond(404, {})

        def do_POST(self) -> None:
            if self.path != "/v1/graphql":
                self.respond(404, {})
                return
            auth = self.headers.get("Authorization", "")
            key = auth.removeprefix("Bearer ")
            if key not in backend.values:
                self.respond(401, {"message": "upstream-sensitive-detail"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 65536:
                self.respond(413, {})
                return
            connection: object = self.connection
            assert isinstance(connection, socket.socket)
            connection.settimeout(5)
            payload = _JSON.validate_json(self.rfile.read(size))
            self.respond(200, backend.dispatch(key, payload))

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        backend.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield backend
        finally:
            server.shutdown()
            thread.join(timeout=5)
