#!/usr/bin/env python3
"""Container liveness probe for the mcp-bridge image.

The bridge only makes outbound connections (WebSocket to the endpoint server,
plus stdio/SSE/HTTP to the MCP server), so there is no listening port to probe.
Inside the image the meaningful signal is that the bridge process is still alive.
"""
from __future__ import annotations

import os


def bridge_running(proc_root: str = "/proc") -> bool:
    """Return True when some process under ``proc_root`` runs mcp_bridge.py."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as handle:
                if b"mcp_bridge.py" in handle.read():
                    return True
        except OSError:
            continue
    return False


def main() -> int:
    return 0 if bridge_running() else 1


if __name__ == "__main__":
    raise SystemExit(main())
