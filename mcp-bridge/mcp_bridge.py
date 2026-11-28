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
import math
import os
import re
import shlex
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx2
import websockets
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, jsonrpc_message_adapter  # noqa: F401  再导出保留（测试引用）

LOG = logging.getLogger("mcp-bridge")

__version__ = "0.0.8"

# 启动日志中一并展示的依赖（发行包名，而非导入名）。
_RUNTIME_PACKAGES = ("mcp", "httpx2", "websockets")


def runtime_versions() -> dict[str, str]:
    """已安装的关键依赖版本；缺失时记为 unknown，不让版本展示影响启动。"""
    from importlib.metadata import PackageNotFoundError, version as package_version

    found = {"bridge": __version__}
    for name in _RUNTIME_PACKAGES:
        try:
            found[name] = package_version(name)
        except PackageNotFoundError:
            found[name] = "unknown"
    return found


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
        if max_size is not None and max_size < 0:
            raise ConfigError("MCP_MAX_SIZE must be >= 0 (0 disables the limit)")
        if max_size == 0:
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
            # Keep an explicitly supplied mapping isolated from the host env so
            # tests and embedded callers do not inherit unrelated secrets.
            base_env = dict(os.environ) if env is None else dict(env)
            merged = dict(base_env)
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
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} must be a finite number")
    return value


def _optional_float(env: Mapping[str, str], key: str, default: float | None) -> float | None:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} must be a finite number")
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
    try:
        return _split_shell_args(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{key} must be a JSON array or a shell-like string"
        ) from exc


def _split_shell_args(raw: str) -> list[str]:
    """Split a shell-like MCP_ARGS string without mangling Windows paths.

    POSIX shlex treats backslashes as escapes, so C:\\dir\\server.py loses its
    separators on Windows. Parse without unquoting there and strip one layer of
    surrounding quotes from each token. A JSON array avoids the ambiguity and is
    the recommended form on every platform.
    """
    if os.name != "nt":
        return shlex.split(raw)
    tokens = shlex.split(raw, posix=False)
    stripped: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        stripped.append(token)
    return stripped


def redact_url(url: str) -> str:
    """Hide token-like query parameters before logging a URL."""
    try:
        parts = urlsplit(url)
        # 用户名/密码（user:pass@host）同样属于凭据，一并打码。
        netloc = parts.netloc
        if "@" in netloc:
            netloc = "***@" + netloc.rsplit("@", 1)[1]
        query = []
        for item in parts.query.split("&") if parts.query else []:
            key, sep, _value = item.partition("=")
            if sep and key.lower() in {
                "token",
                "key",
                "api_key",
                "apikey",
                "access_token",
                "secret",
                "password",
                "sig",
                "signature",
            }:
                query.append(f"{key}=***")
            else:
                query.append(item)
        return urlunsplit(parts._replace(netloc=netloc, query="&".join(query)))
    except Exception:
        return "<invalid-url>"


_SECRET_TEXT_RE = re.compile(
    r"(?i)(token|key|api_key|apikey|access_token|secret|password|sig|signature)=([^&\s\"']+)"
)


def redact_text(text: Any) -> str:
    """Mask token-like parameters in free-form text such as exception messages."""
    return _SECRET_TEXT_RE.sub(r"\1=***", str(text))


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
        # mcp 2.x takes a pre-configured httpx2 client instead of headers and
        # timeouts, and the transport yields exactly (read_stream, write_stream).
        async with http_client_factory(headers=cfg.headers) as http_client:
            async with streamablehttp_client(
                cfg.url, http_client=http_client
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
    """Build an httpx2 client factory that respects MCP_HTTP_TRUST_ENV.

    httpx2 reads proxy settings from the environment by default.  A malformed
    NO_PROXY entry (for example a bare IPv6 address) makes httpx2 raise while
    constructing the client, before any MCP request is sent.  MCP servers are
    usually local or internal, so environment proxies are off by default and
    can be enabled explicitly with MCP_HTTP_TRUST_ENV=1.
    """

    def factory(headers=None, timeout=None, auth=None):
        if timeout is None:
            timeout = httpx2.Timeout(cfg.timeout, read=cfg.sse_read_timeout)
        kwargs = {"timeout": timeout, "trust_env": cfg.trust_env}
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx2.AsyncClient(**kwargs)

    return factory


def _dump_message(message: Any) -> str:
    """Serialize an MCP message to JSON-RPC wire text.

    Only null-valued id / error / params fields are dropped: a notification has
    no id, a success response must not carry a null error, and JSON-RPC 2.0
    requires params (when present) to be a structured value, so a request or
    notification without arguments must omit it instead of sending params:null.
    result is kept even when it is null: {"jsonrpc":"2.0","id":1,"result":null}
    is a valid response, and stripping it would leave neither result nor error.
    """
    data = json.loads(message.model_dump_json(by_alias=True))
    for key in ("id", "error", "params"):
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


class _RawJSONRPCMessage:
    """Verbatim wrapper for a JSON-RPC message the pinned SDK cannot type.

    JSONRPCResponse.result is declared as dict in the SDK we pin, so a legal
    {"id": 1, "result": null} fails validation on the way in. All transports
    serialize outbound messages with exclude_none=True, which would drop
    result:null and produce an invalid response, so this wrapper returns the
    parsed body exactly as received instead.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self._data)

    def model_dump_json(self, **_kwargs: Any) -> str:
        return json.dumps(self._data, ensure_ascii=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_RawJSONRPCMessage({self._data!r})"


def _is_null_result_response(data: Any) -> bool:
    """True for a well-formed JSON-RPC response whose result is null."""
    if not isinstance(data, dict):
        return False
    if data.get("jsonrpc") != "2.0" or "method" in data:
        return False
    if "id" not in data or "error" in data:
        return False
    return "result" in data and data["result"] is None


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
        except (json.JSONDecodeError, RecursionError):
            LOG.warning("[%s] ignored non-JSON WebSocket message: %s", name, str(raw)[:200])
            continue
        try:
            message: Any = jsonrpc_message_adapter.validate_python(data)
        except Exception as exc:  # pydantic validation errors
            if _is_null_result_response(data):
                LOG.warning(
                    "[%s] passing through response with result=null (SDK cannot type it): id=%s",
                    name,
                    data.get("id"),
                )
                message = _RawJSONRPCMessage(data)
            else:
                LOG.warning("[%s] ignored invalid JSON-RPC message: %s", name, exc)
                continue
        await write_stream.send(SessionMessage(message=message))  # type: ignore[arg-type]
        LOG.debug("[%s] ws -> remote: %s", name, str(raw)[:800])


async def run_once(cfg: BridgeConfig, stop_event: asyncio.Event | None = None) -> None:
    """Run one WebSocket + transport lifetime.

    Returns when either side closes, or as soon as stop_event is set so a
    SIGINT/SIGTERM during an active connection tears down immediately instead
    of waiting for the transport to fail on its own.
    """
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
            stop_task: asyncio.Task[bool] | None = None
            waiting = set(tasks)
            if stop_event is not None:
                stop_task = asyncio.create_task(
                    stop_event.wait(), name=f"{cfg.name}-stop"
                )
                waiting.add(stop_task)
            try:
                done, _ = await asyncio.wait(
                    waiting, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if stop_task is not None:
                    stop_task.cancel()
                    await asyncio.gather(stop_task, return_exceptions=True)
            for task in done:
                if task is stop_task:
                    LOG.info("[%s] stop requested; closing active connection", cfg.name)
                    continue
                exc = task.exception()
                if exc is not None:
                    raise exc


async def run_forever(cfg: BridgeConfig, stop_event: asyncio.Event) -> None:
    """Reconnect with exponential backoff until stopped."""
    backoff = cfg.reconnect_initial
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            await run_once(cfg, stop_event)
            # 正常返回（连接干净关闭）视为一次成功，重置退避，避免快速重连时退避翻倍。
            backoff = cfg.reconnect_initial
            if not stop_event.is_set():
                LOG.warning("[%s] bridge stopped (one side closed)", cfg.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 异常文本里可能带完整 MCP_URL（含 token），不能原样写日志。
            LOG.warning(
                "[%s] bridge error: %s: %s", cfg.name, type(exc).__name__, redact_text(exc)
            )
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


_ARG_SECRET_FLAGS = frozenset({
    "--token",
    "--key",
    "--api-key",
    "--api_key",
    "--apikey",
    "--access-token",
    "--access_token",
    "--secret",
    "--password",
})


def _redact_args(args: list[str]) -> list[str]:
    """Mask values of token-like CLI flags before printing a config.

    Secrets are often passed positionally (--token value) or inline
    (--token=value); both forms are covered so --print-config stays safe.
    """
    redacted: list[str] = []
    mask_next = False
    for item in args:
        if mask_next:
            redacted.append("***")
            mask_next = False
            continue
        flag, sep, _value = item.partition("=")
        if flag.lower() in _ARG_SECRET_FLAGS:
            if sep:
                redacted.append(flag + "=***")
            else:
                redacted.append(item)
                mask_next = True
            continue
        redacted.append(item)
    return redacted


def _public_config(cfg: BridgeConfig) -> dict[str, Any]:
    data = asdict(cfg)
    data["version"] = __version__
    data["endpoint"] = redact_url(cfg.endpoint)
    data["args"] = _redact_args(cfg.args)
    if cfg.url:
        # asdict 里的 url 原样包含 token/userinfo，必须同样脱敏。
        data["url"] = redact_url(cfg.url)
    data["headers"] = dict.fromkeys(cfg.headers, "***")
    if cfg.env is not None:
        data["env"] = dict.fromkeys(cfg.env, "***")
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
    versions = runtime_versions()
    LOG.info(
        "[%s] mcp-bridge %s (mcp %s, httpx2 %s, websockets %s) starting "
        "transport=%s endpoint=%s",
        cfg.name,
        versions["bridge"],
        versions["mcp"],
        versions["httpx2"],
        versions["websockets"],
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
