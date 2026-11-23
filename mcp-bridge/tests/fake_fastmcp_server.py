#!/usr/bin/env python3
"""Tiny FastMCP server used by the bridge integration tests."""

from __future__ import annotations

import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP

    _SERVER_TAKES_HOST = True
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

    _SERVER_TAKES_HOST = False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: fake_fastmcp_server.py <transport> <port>", file=sys.stderr)
        return 2
    transport = argv[0]
    try:
        port = int(argv[1])
    except ValueError:
        print(f"invalid port: {argv[1]!r}", file=sys.stderr)
        return 2
    if _SERVER_TAKES_HOST:
        server = FastMCP("fake-fastmcp")
    else:
        server = FastMCP("fake-fastmcp", host="127.0.0.1", port=port)

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back with a prefix."""
        return "echo:" + text

    run_kwargs = {"host": "127.0.0.1", "port": port} if _SERVER_TAKES_HOST else {}
    server.run(transport=transport, **run_kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
