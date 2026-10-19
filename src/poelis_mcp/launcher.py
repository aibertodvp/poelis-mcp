"""Local staging launcher. Personal SDK credentials stay in process memory."""

import asyncio
import fcntl
import getpass
import os
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from poelis_mcp.sdk import SDKAdapter, SDKError

COMMANDS = ("start", "stop", "status")


class Connection(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    resource_url: str
    expires_at: int


def load_session(directory: Path) -> Connection:
    try:
        connection = Connection.model_validate_json((directory / "connection.json").read_bytes())
        url = urlsplit(connection.resource_url)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.path != "/mcp"
            or url.port is None
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or connection.expires_at <= time.time()
            or not (directory / "token").is_file()
            or (directory / "stop").exists()
        ):
            raise ValueError("Inactive session")
        return connection
    except OSError, ValueError:
        raise ValueError(
            "No active session. Run uv run --locked python -m poelis_mcp start."
        ) from None


def atomic_private_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def request_stop(directory: Path) -> None:
    if not (directory / "connection.json").exists():
        return
    (directory / "stop").touch(mode=0o600, exist_ok=True)
    deadline = time.monotonic() + 10
    while (directory / "connection.json").exists() or (directory / "token").exists():
        if time.monotonic() >= deadline:
            raise ValueError("Server has not stopped; no unrelated process was signalled.")
        time.sleep(0.05)


def state_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    root = base / "poelis-mcp"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


@contextmanager
def setup_lock(root: Path) -> Iterator[None]:
    descriptor = os.open(root / "setup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another staging setup command is running.") from None
        yield


def active_directory(root: Path) -> Path | None:
    pointer = root / "active"
    if not pointer.exists():
        return None
    directory = Path(pointer.read_text().strip())
    if directory.parent != root or not directory.name.startswith("session_"):
        raise ValueError("Invalid local staging state.")
    return directory


def running_connection(directory: Path) -> Connection:
    connection = load_session(directory)
    try:
        response = httpx.post(
            connection.resource_url,
            content=b"{}",
            timeout=2,
            trust_env=False,
            headers={"Content-Type": "application/json"},
            follow_redirects=False,
        )
        if response.status_code != 401:
            raise ValueError("Unexpected local server response.")
    except httpx.HTTPError:
        raise ValueError(
            "Server not running. Run uv run --locked python -m poelis_mcp start."
        ) from None
    return connection


async def validate_key(key: str) -> None:
    client = SDKAdapter(SecretStr(key), base_url="https://api-staging.poelis.com")
    try:
        await client.execute("list_workspaces", {"limit": 1, "offset": 0})
    finally:
        await client.aclose()


def launch(root: Path, key: str) -> tuple[Path, Connection]:
    directory = root / ("session_" + uuid.uuid4().hex)
    log_path = root / "server.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    environment = dict(os.environ)
    environment["POELIS_SDK_KEY"] = key
    with os.fdopen(descriptor, "w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "poelis_mcp.session",
                "--session-dir",
                str(directory),
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 15
        while not (directory / "connection.json").exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise ValueError("Staging server failed to start; check the private server.log.")
            time.sleep(0.05)
        connection = running_connection(directory)
        atomic_private_write(root / "active", str(directory))
        return directory, connection
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise


def show_connection(directory: Path, connection: Connection) -> None:
    print(f"MCP URL: {connection.resource_url}")
    print("Transport: Streamable HTTP; authentication: Authorization: Bearer <token>")
    print(f"Bearer token file: {directory / 'token'} (private; do not share)")


def start(root: Path) -> None:
    previous = active_directory(root)
    if previous is not None:
        try:
            connection = running_connection(previous)
        except ValueError:
            # Shut down an owned old-format/expired session before replacing it.
            request_stop(previous)
        else:
            print("Staging server is already running.")
            show_connection(previous, connection)
            return
    key = os.environ.get("POELIS_SDK_KEY")
    if not key:
        if not os.isatty(0):
            raise ValueError("Run interactively or supply POELIS_SDK_KEY in the environment.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Personal staging SDK key (hidden; paste and press Enter): ")
    if not key.strip():
        raise ValueError("The staging SDK key is empty.")
    asyncio.run(validate_key(key))
    directory, connection = launch(root, key)
    print("Staging server is running in background.")
    show_connection(directory, connection)
    print("Access follows this SDK key's Poelis permissions. Expires after four hours.")
    print("Stop server: uv run --locked python -m poelis_mcp stop")


def run(command: str) -> int:
    try:
        root = state_root()
        with setup_lock(root):
            if command == "start":
                start(root)
            else:
                directory = active_directory(root)
                if directory is None:
                    print("No staging session is running.")
                elif command == "stop":
                    request_stop(directory)
                    (root / "active").unlink(missing_ok=True)
                    print("Staging server stopped. Poelis objects were not modified.")
                else:
                    connection = running_connection(directory)
                    show_connection(directory, connection)
    except SDKError as error:
        print(f"Staging SDK error: {error.code}. No credentials were saved.", file=sys.stderr)
        return 1
    except OSError, getpass.GetPassWarning:
        print(
            "Setup failed: check local paths, permissions and terminal.",
            file=sys.stderr,
        )
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except EOFError, KeyboardInterrupt:
        print("Setup cancelled.", file=sys.stderr)
        return 1
    return 0
