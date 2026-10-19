"""Run the authenticated Streamable HTTP service; never accept keys as CLI arguments."""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from poelis_mcp.config import load_settings
from poelis_mcp.http import BoundedH11Protocol
from poelis_mcp.launcher import COMMANDS, run
from poelis_mcp.server import create_app


class Arguments(argparse.Namespace):
    config: str | None
    command: str | None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m poelis_mcp", description="Poelis MCP server")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("command", nargs="?", choices=COMMANDS, help="Manage a local staging session")
    mode.add_argument("--config", help="Serve an operator-managed JSON configuration in foreground")
    args = Arguments()
    parser.parse_args(namespace=args)
    if args.command is not None:
        raise SystemExit(run(args.command))
    assert args.config is not None
    try:
        settings = load_settings(Path(args.config))
        app = create_app(settings)
    except OSError, ValueError, ValidationError:
        # Config/validation exceptions may carry input values. Never print them.
        print("Invalid configuration or missing credential environment variable.", file=sys.stderr)
        raise SystemExit(2) from None
    protocol: type[asyncio.Protocol] = BoundedH11Protocol
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        access_log=False,
        log_level="warning",
        http=protocol,
        ws="none",
        limit_concurrency=100,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
