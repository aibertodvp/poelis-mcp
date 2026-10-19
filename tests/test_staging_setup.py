"""Local onboarding behavior; no live Poelis credentials or network calls."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from poelis_mcp import launcher
from poelis_mcp.launcher import (
    Connection,
    launch,
    load_session,
    request_stop,
)


def session(tmp_path: Path, *, expired: bool = False) -> Path:
    directory = tmp_path / "session"
    directory.mkdir(mode=0o700)
    connection = Connection(
        resource_url="http://127.0.0.1:12345/mcp",
        expires_at=int(time.time()) + (-10 if expired else 300),
    )
    (directory / "connection.json").write_text(connection.model_dump_json())
    (directory / "token").write_text("local-test-token")
    return directory


@pytest.mark.parametrize("state", ["expired", "missing_token", "stopped"])
def test_unusable_sessions_cannot_be_reused(tmp_path: Path, state: str) -> None:
    directory = session(tmp_path, expired=state == "expired")
    if state == "missing_token":
        (directory / "token").unlink()
    if state == "stopped":
        (directory / "stop").touch()
    with pytest.raises(ValueError):
        load_session(directory)


def test_stop_shuts_down_owned_background_session(tmp_path: Path) -> None:
    directory = tmp_path / "session"
    environment = dict(os.environ)
    environment["POELIS_SDK_KEY"] = "local-lifecycle-test-not-a-live-key"
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-m",
            "poelis_mcp.session",
            "--session-dir",
            str(directory),
            "--duration-seconds",
            "30",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (directory / "connection.json").exists():
            assert process.poll() is None, "Local harness failed to start"
            assert time.monotonic() < deadline, "Local harness startup timed out"
            time.sleep(0.02)
        load_session(directory)
        request_stop(directory)
        assert process.wait(timeout=10) == 0
        assert not (directory / "token").exists()
        assert not (directory / "connection.json").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_background_launch_rolls_back_on_state_write_failure(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    (root / "active").mkdir()
    key = "local-rollback-test-not-a-live-key"
    with pytest.raises(OSError):
        launch(root, key)
    assert not list(root.glob("session_*/connection.json"))
    assert not list(root.glob("session_*/token"))
    assert key not in (root / "server.log").read_text()


def test_installed_launcher_without_repo_cwd_or_external_runners(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    settings = home / "client.json"
    original = '{"existing_servers": ["other"], "approval_mode": "user-choice"}\n'
    settings.write_text(original)
    key = "local-client-independent-test-not-a-live-key"
    environment = dict(os.environ)
    environment.update(
        PATH="",
        HOME=str(home),
        XDG_STATE_HOME=str(tmp_path / "state"),
        POELIS_SDK_KEY=key,
    )
    # Exercise the CLI in its own process, as used for a detached server.
    # Only upstream discovery is substituted; startup, HTTP auth and shutdown are real.
    script = (
        "from poelis_mcp import launcher\n"
        "from poelis_mcp.__main__ import main\n"
        "async def validate_key(credential: str) -> None:\n"
        "    return None\n"
        "launcher.validate_key = validate_key\n"
        "main()\n"
    )
    root = tmp_path / "state" / "poelis-mcp"
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, "start"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        directory = launcher.active_directory(root)
        assert directory is not None
        connection = launcher.running_connection(directory)
        output = result.stdout + result.stderr
        assert connection.resource_url in output
        assert str(directory / "token") in output
        assert key not in output
        assert (directory / "token").read_text() not in output
        assert settings.read_text() == original

        environment.pop("POELIS_SDK_KEY")
        subprocess.run(
            [sys.executable, "-I", "-m", "poelis_mcp", "start"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            timeout=10,
            check=True,
        )
        assert launcher.active_directory(root) == directory
        assert settings.read_text() == original
        status = subprocess.run(
            [sys.executable, "-I", "-m", "poelis_mcp", "status"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert connection.resource_url in status.stdout
        assert key not in status.stdout
        subprocess.run(
            [sys.executable, "-I", "-m", "poelis_mcp", "stop"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            timeout=15,
            check=True,
        )
        assert not (directory / "token").exists()
        assert launcher.active_directory(root) is None
        assert list(home.iterdir()) == [settings]
        assert settings.read_text() == original
    finally:
        directory = launcher.active_directory(root)
        if directory is not None:
            request_stop(directory)


def test_local_issuer_exposes_only_public_jwks() -> None:
    import httpx2 as httpx

    from poelis_mcp.session import local_issuer

    with local_issuer() as (url, _key):
        with httpx.Client(trust_env=False) as client:
            response = client.get(url + "/jwks")
            assert response.status_code == 200
            original = response.content
            document = TypeAdapter(dict[str, list[dict[str, str]]]).validate_json(response.content)
            assert len(document["keys"]) == 1
            assert set(document["keys"][0]) == {"kty", "use", "alg", "kid", "n", "e"}
            marker = "caller-credential-must-not-be-recorded"
            response = client.get(url + "/v1/graphql", headers={"Authorization": marker})
            assert response.status_code == 404 and marker not in response.text
            assert client.post(url + "/jwks", content=marker).status_code == 405
            assert client.request("GET", url + "/jwks", content=marker).status_code == 400
            assert client.get(url + "/jwks").content == original
