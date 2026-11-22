#!/usr/bin/env python3
"""Tiny FastMCP server used by the bridge integration tests."""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP


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
    server = FastMCP("fake-fastmcp", host="127.0.0.1", port=port)

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back with a prefix."""
        return "echo:" + text

    server.run(transport=transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
