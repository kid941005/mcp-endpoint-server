#!/usr/bin/env python3
"""MCP Bridge: expose stdio, SSE, or Streamable HTTP MCP servers as an
mcp-endpoint-server tool endpoint.

The bridge is a transport converter.  It opens a WebSocket connection to the
endpoint server's tool side (/mcp_endpoint/mcp/) and relays raw JSON-RPC
messages to/from one MCP server.  It deliberately does not implement an MCP
client session: initialize, tools/list, tools/call and all notifications are
passed through unchanged, so protocol negotiation remains end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shlex
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

LOG = logging.getLogger("mcp-bridge")

DEFAULT_MAX_SIZE = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
DEFAULT_SSE_READ_TIMEOUT = 300.0
DEFAULT_RECONNECT_INITIAL = 1.0
DEFAULT_RECONNECT_MAX = 60.0


class ConfigError(ValueError):
    """Raised when bridge configuration is invalid."""


@dataclass
class BridgeConfig:
    endpoint: str
    transport: str
    name: str = "mcp"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    sse_read_timeout: float = DEFAULT_SSE_READ_TIMEOUT
    max_size: int | None = DEFAULT_MAX_SIZE
    reconnect_initial: float = DEFAULT_RECONNECT_INITIAL
    reconnect_max: float = DEFAULT_RECONNECT_MAX
    ping_interval: float | None = 20.0
    ping_timeout: float | None = 20.0
    log_level: str = "INFO"
    trust_env: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BridgeConfig":
        source = dict(os.environ if env is None else env)

        endpoint = _get(source, "MCP_ENDPOINT")
        if not endpoint:
            raise ConfigError("MCP_ENDPOINT is required (tool-side WebSocket URL)")

        raw_transport = (_get(source, "MCP_TRANSPORT") or "stdio").strip().lower()
        aliases = {
            "stdio": "stdio",
            "sse": "sse",
            "http": "streamable-http",
            "streamable": "streamable-http",
            "streamablehttp": "streamable-http",
            "streamable_http": "streamable-http",
            "streamable-http": "streamable-http",
        }
        transport = aliases.get(raw_transport)
        if transport is None:
            raise ConfigError(
                "MCP_TRANSPORT must be one of: stdio, sse, streamable-http (http)"
            )

        name = _get(source, "MCP_SERVER_NAME") or "mcp"
        headers = _json_object(source, "MCP_HEADERS")
        max_size = _int_value(source, "MCP_MAX_SIZE", DEFAULT_MAX_SIZE)
        if max_size is not None and max_size <= 0:
            max_size = None

        cfg = cls(
            endpoint=endpoint,
            transport=transport,
            name=name,
            headers={str(k): str(v) for k, v in headers.items()},
            timeout=_float_value(source, "MCP_TIMEOUT", DEFAULT_TIMEOUT),
            sse_read_timeout=_float_value(
                source, "MCP_SSE_READ_TIMEOUT", DEFAULT_SSE_READ_TIMEOUT
            ),
            max_size=max_size,
            reconnect_initial=_float_value(
                source, "MCP_RECONNECT_INITIAL", DEFAULT_RECONNECT_INITIAL
            ),
            reconnect_max=_float_value(
                source, "MCP_RECONNECT_MAX", DEFAULT_RECONNECT_MAX
            ),
            ping_interval=_optional_float(source, "MCP_PING_INTERVAL", 20.0),
            ping_timeout=_optional_float(source, "MCP_PING_TIMEOUT", 20.0),
            log_level=(_get(source, "MCP_LOG_LEVEL") or "INFO").upper(),
            trust_env=_bool_value(source, "MCP_HTTP_TRUST_ENV", False),
        )

        if transport == "stdio":
            cfg.command = _get(source, "MCP_COMMAND")
            if not cfg.command:
                raise ConfigError("MCP_COMMAND is required for stdio transport")
            cfg.args = _args_value(source, "MCP_ARGS")
            cfg.cwd = _get(source, "MCP_CWD") or None
            overrides = _json_object(source, "MCP_ENV")
            merged = dict(os.environ)
            merged.update({str(k): str(v) for k, v in overrides.items()})
            cfg.env = merged
        else:
            cfg.url = _get(source, "MCP_URL")
            if not cfg.url:
                raise ConfigError("MCP_URL is required for sse/streamable-http transport")

        if cfg.reconnect_initial <= 0:
            raise ConfigError("MCP_RECONNECT_INITIAL must be > 0")
        if cfg.reconnect_max < cfg.reconnect_initial:
            raise ConfigError("MCP_RECONNECT_MAX must be >= MCP_RECONNECT_INITIAL")
        if cfg.timeout <= 0:
            raise ConfigError("MCP_TIMEOUT must be > 0")
        if cfg.sse_read_timeout <= 0:
            raise ConfigError("MCP_SSE_READ_TIMEOUT must be > 0")
        return cfg


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _float_value(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc


def _optional_float(env: Mapping[str, str], key: str, default: float | None) -> float | None:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    return None if value <= 0 else value


def _int_value(env: Mapping[str, str], key: str, default: int | None) -> int | None:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _bool_value(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key)
    if raw is None:
        return default
    value = raw.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean (1/0, true/false)")


def _json_object(env: Mapping[str, str], key: str) -> dict[str, Any]:
    raw = _get(env, key)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{key} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a JSON object")
    return value


def _args_value(env: Mapping[str, str], key: str) -> list[str]:
    raw = _get(env, key)
    if raw is None:
        return []
    if raw.startswith("["):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{key} must be a JSON array or a shell-like string") from exc
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ConfigError(f"{key} must be a JSON array of strings")
        return value
    return shlex.split(raw)


def redact_url(url: str) -> str:
    """Hide token-like query parameters before logging a URL."""
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        query = []
        for item in parts.query.split("&"):
            key, sep, _value = item.partition("=")
            if sep and key.lower() in {
                "token",
                "key",
                "api_key",
                "apikey",
                "access_token",
                "secret",
            }:
                query.append(f"{key}=***")
            else:
                query.append(item)
        return urlunsplit(parts._replace(query="&".join(query)))
    except Exception:
        return "<invalid-url>"


@contextlib.asynccontextmanager
async def open_transport(cfg: BridgeConfig) -> AsyncIterator[tuple[Any, Any]]:
    """Open the selected MCP transport and yield (read_stream, write_stream)."""
    if cfg.transport == "stdio":
        assert cfg.command is not None
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            cwd=cfg.cwd,
        )
        async with stdio_client(params) as streams:
            yield streams[0], streams[1]
        return

    assert cfg.url is not None
    http_client_factory = _make_httpx_client_factory(cfg)
    if cfg.transport == "sse":
        async with sse_client(
            cfg.url,
            headers=cfg.headers,
            timeout=cfg.timeout,
            sse_read_timeout=cfg.sse_read_timeout,
            httpx_client_factory=http_client_factory,
        ) as streams:
            yield streams[0], streams[1]
        return

    if cfg.transport == "streamable-http":
        async with streamablehttp_client(
            cfg.url,
            headers=cfg.headers,
            timeout=cfg.timeout,
            sse_read_timeout=cfg.sse_read_timeout,
            httpx_client_factory=http_client_factory,
        ) as streams:
            if len(streams) >= 3:
                try:
                    session_id = streams[2]()
                except Exception:
                    session_id = None
                if session_id:
                    LOG.info("[%s] Streamable HTTP session: %s", cfg.name, session_id)
            yield streams[0], streams[1]
        return

    raise ConfigError(f"unsupported transport: {cfg.transport}")


def _make_httpx_client_factory(cfg: BridgeConfig):
    """Build an httpx client factory that respects MCP_HTTP_TRUST_ENV.

    httpx reads proxy settings from the environment by default.  A malformed
    NO_PROXY entry (for example a bare IPv6 address) makes httpx raise while
    constructing the client, before any MCP request is sent.  MCP servers are
    usually local or internal, so environment proxies are off by default and
    can be enabled explicitly with MCP_HTTP_TRUST_ENV=1.
    """

    def factory(headers=None, timeout=None, auth=None):
        if timeout is None:
            timeout = httpx.Timeout(cfg.timeout, read=cfg.sse_read_timeout)
        kwargs = {"timeout": timeout, "trust_env": cfg.trust_env}
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory


def _dump_message(message: Any) -> str:
    """Serialize an MCP message to JSON-RPC wire text.

    Only null-valued id / error fields are dropped (a notification has no id,
    and a success response must not carry a null error).  result is kept even
    when it is null: {"jsonrpc":"2.0","id":1,"result":null} is a valid response,
    and stripping it would leave a message with neither result nor error.
    """
    data = json.loads(message.model_dump_json(by_alias=True))
    for key in ("id", "error"):
        if data.get(key) is None:
            data.pop(key, None)
    return json.dumps(data, ensure_ascii=False)


async def relay_remote_to_ws(read_stream: Any, websocket: Any, name: str) -> None:
    """Forward MCP server messages to the endpoint WebSocket."""
    async for item in read_stream:
        if isinstance(item, Exception):
            raise item
        payload = _dump_message(item.message)
        await websocket.send(payload)
        LOG.debug("[%s] remote -> ws: %s", name, payload[:800])


async def relay_ws_to_remote(websocket: Any, write_stream: Any, name: str) -> None:
    """Forward endpoint WebSocket JSON-RPC messages to the MCP server."""
    async for raw in websocket:
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                LOG.warning("[%s] ignored non-UTF-8 binary WebSocket message", name)
                continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("[%s] ignored non-JSON WebSocket message: %s", name, str(raw)[:200])
            continue
        try:
            message = JSONRPCMessage.model_validate(data)
        except Exception as exc:  # pydantic validation errors
            LOG.warning("[%s] ignored invalid JSON-RPC message: %s", name, exc)
            continue
        await write_stream.send(SessionMessage(message=message))
        LOG.debug("[%s] ws -> remote: %s", name, str(raw)[:800])


async def run_once(cfg: BridgeConfig) -> None:
    """Run one WebSocket + transport lifetime. Returns when either side closes."""
    LOG.info("[%s] connecting endpoint: %s", cfg.name, redact_url(cfg.endpoint))
    async with websockets.connect(
        cfg.endpoint,
        max_size=cfg.max_size,
        open_timeout=cfg.timeout,
        ping_interval=cfg.ping_interval,
        ping_timeout=cfg.ping_timeout,
    ) as websocket:
        LOG.info("[%s] endpoint connected; opening %s transport", cfg.name, cfg.transport)
        async with open_transport(cfg) as (read_stream, write_stream):
            tasks = [
                asyncio.create_task(
                    relay_remote_to_ws(read_stream, websocket, cfg.name),
                    name=f"{cfg.name}-remote-to-ws",
                ),
                asyncio.create_task(
                    relay_ws_to_remote(websocket, write_stream, cfg.name),
                    name=f"{cfg.name}-ws-to-remote",
                ),
            ]
            try:
                done, _ = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc


async def run_forever(cfg: BridgeConfig, stop_event: asyncio.Event) -> None:
    """Reconnect with exponential backoff until stopped."""
    backoff = cfg.reconnect_initial
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            await run_once(cfg)
            LOG.warning("[%s] bridge stopped (one side closed)", cfg.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("[%s] bridge error: %s: %s", cfg.name, type(exc).__name__, exc)
            LOG.debug("[%s] bridge traceback", cfg.name, exc_info=True)
        if stop_event.is_set():
            break
        elapsed = time.monotonic() - started
        if elapsed >= 10.0:
            backoff = cfg.reconnect_initial
        LOG.info("[%s] reconnecting in %.1fs", cfg.name, backoff)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2.0, cfg.reconnect_max)


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, lambda *_args: loop.call_soon_threadsafe(stop_event.set))
            except (ValueError, OSError):
                pass


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _public_config(cfg: BridgeConfig) -> dict[str, Any]:
    data = asdict(cfg)
    data["endpoint"] = redact_url(cfg.endpoint)
    data["headers"] = {key: "***" for key in cfg.headers}
    if cfg.env is not None:
        data["env"] = {key: "***" for key in cfg.env}
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP transport bridge for mcp-endpoint-server")
    parser.add_argument("--print-config", action="store_true", help="print effective config and exit")
    parser.add_argument("--log-level", help="override MCP_LOG_LEVEL")
    args = parser.parse_args(argv)

    try:
        cfg = BridgeConfig.from_env()
        if args.log_level:
            cfg.log_level = args.log_level.upper()
        if args.print_config:
            print(json.dumps(_public_config(cfg), ensure_ascii=False, indent=2))
            return 0
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(cfg.log_level)
    LOG.info(
        "[%s] starting transport=%s endpoint=%s",
        cfg.name,
        cfg.transport,
        redact_url(cfg.endpoint),
    )

    async def _main() -> None:
        stop_event = asyncio.Event()
        install_signal_handlers(stop_event)
        await run_forever(cfg, stop_event)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        LOG.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
