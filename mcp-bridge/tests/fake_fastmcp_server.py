#!/usr/bin/env python3
"""Tiny FastMCP server used by the bridge integration tests."""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP


def main() -> int:
    transport = sys.argv[1]
    port = int(sys.argv[2])
    server = FastMCP("fake-fastmcp", host="127.0.0.1", port=port)

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back with a prefix."""
        return "echo:" + text

    server.run(transport=transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
