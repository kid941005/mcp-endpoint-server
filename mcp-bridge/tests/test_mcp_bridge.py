from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_bridge  # noqa: E402
import healthcheck as mcp_bridge_healthcheck  # noqa: E402

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None


class ConfigTests(unittest.TestCase):
    def test_stdio_config(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_TRANSPORT": "stdio",
            "MCP_COMMAND": "python",
            "MCP_ARGS": '["server.py", "--flag"]',
            "MCP_SERVER_NAME": "demo",
            "MCP_ENV": '{"FOO":"bar"}',
        })
        self.assertEqual(cfg.transport, "stdio")
        self.assertEqual(cfg.command, "python")
        self.assertEqual(cfg.args, ["server.py", "--flag"])
        self.assertEqual(cfg.name, "demo")
        self.assertEqual(cfg.env["FOO"], "bar")

    def test_sse_config(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_TRANSPORT": "sse",
            "MCP_URL": "http://127.0.0.1:8080/sse",
            "MCP_HEADERS": '{"Authorization":"Bearer x"}',
            "MCP_TIMEOUT": "12.5",
        })
        self.assertEqual(cfg.transport, "sse")
        self.assertEqual(cfg.url, "http://127.0.0.1:8080/sse")
        self.assertEqual(cfg.headers["Authorization"], "Bearer x")
        self.assertEqual(cfg.timeout, 12.5)

    def test_http_alias(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_TRANSPORT": "http",
            "MCP_URL": "http://127.0.0.1:8080/mcp",
        })
        self.assertEqual(cfg.transport, "streamable-http")

    def test_invalid_transport(self):
        with self.assertRaises(mcp_bridge.ConfigError):
            mcp_bridge.BridgeConfig.from_env({
                "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
                "MCP_TRANSPORT": "carrier-pigeon",
            })

    def test_redact_url(self):
        url = "wss://example.test/mcp_endpoint/mcp/?token=" + "abc" + "123" + "&x=1"
        redacted = mcp_bridge.redact_url(url)
        self.assertNotIn("abc123", redacted)
        self.assertIn("token=***", redacted)
        self.assertIn("x=1", redacted)

    def test_http_trust_env_config(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_TRANSPORT": "streamable-http",
            "MCP_URL": "http://127.0.0.1:8080/mcp",
            "MCP_HTTP_TRUST_ENV": "1",
        })
        self.assertTrue(cfg.trust_env)

    def test_args_shell_string(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
            "MCP_ARGS": "server.py --flag value",
        })
        self.assertEqual(cfg.args, ["server.py", "--flag", "value"])

    def test_unclosed_quote_in_args_is_a_config_error(self):
        with self.assertRaises(mcp_bridge.ConfigError):
            mcp_bridge.BridgeConfig.from_env({
                "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
                "MCP_COMMAND": "python",
                "MCP_ARGS": 'server.py "--unterminated',
            })

    def test_print_config_redacts_secret_args(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
            "MCP_ARGS": '["server.py", "--token", "s3cret", "--key=inline", "--verbose"]',
        })
        args = mcp_bridge._public_config(cfg)["args"]
        self.assertEqual(
            args, ["server.py", "--token", "***", "--key=***", "--verbose"]
        )
        self.assertNotIn("s3cret", str(args))
        self.assertNotIn("inline", str(args))


class TransportSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_transport_parameters(self):
        calls = []

        @contextlib.asynccontextmanager
        async def fake_sse(*args, **kwargs):
            calls.append((args, kwargs))
            yield object(), object()

        cfg = mcp_bridge.BridgeConfig(
            endpoint="ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            transport="sse",
            url="http://127.0.0.1:8080/sse",
            headers={"Authorization": "Bearer x"},
            timeout=7,
            sse_read_timeout=8,
        )
        with patch.object(mcp_bridge, "sse_client", fake_sse):
            async with mcp_bridge.open_transport(cfg) as streams:
                self.assertEqual(len(streams), 2)
        self.assertEqual(calls[0][0], ("http://127.0.0.1:8080/sse",))
        self.assertEqual(calls[0][1]["headers"], {"Authorization": "Bearer x"})
        self.assertEqual(calls[0][1]["timeout"], 7)
        self.assertEqual(calls[0][1]["sse_read_timeout"], 8)

    async def test_streamable_http_transport_parameters(self):
        calls = []

        @contextlib.asynccontextmanager
        async def fake_http(*args, **kwargs):
            calls.append((args, kwargs))
            yield object(), object(), lambda: "session-1"

        cfg = mcp_bridge.BridgeConfig(
            endpoint="ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            transport="streamable-http",
            url="http://127.0.0.1:8080/mcp",
            headers={"X-Test": "1"},
            timeout=7,
            sse_read_timeout=8,
        )
        with patch.object(mcp_bridge, "streamablehttp_client", fake_http):
            async with mcp_bridge.open_transport(cfg) as streams:
                self.assertEqual(len(streams), 2)
        self.assertEqual(calls[0][0], ("http://127.0.0.1:8080/mcp",))
        client = calls[0][1]["http_client"]
        self.assertEqual(client.headers.get("X-Test"), "1")
        self.assertEqual(client.timeout.read, 8.0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} did not open")


async def _terminate(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


@unittest.skipIf(websockets is None, "websockets is not installed")
class StdioIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_end_to_end(self):
        fake_server = ROOT / "tests" / "fake_stdio_server.py"
        result = asyncio.get_running_loop().create_future()

        async def handler(websocket):
            try:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }))
                init_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert init_response["id"] == 1
                assert init_response["result"]["serverInfo"]["name"] == "fake-stdio"

                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
                }))
                tools_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert tools_response["result"]["tools"][0]["name"] == "echo"

                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"text": "hello"}},
                }))
                call_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert call_response["result"]["content"][0]["text"] == "echo:hello"
                result.set_result(call_response)
            except Exception as exc:  # noqa: BLE001
                if not result.done():
                    result.set_exception(exc)
            finally:
                await websocket.close()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        env = dict(os.environ)
        env.update({
            "MCP_ENDPOINT": f"ws://127.0.0.1:{port}/mcp_endpoint/mcp/?token=test",
            "MCP_TRANSPORT": "stdio",
            "MCP_COMMAND": sys.executable,
            "MCP_ARGS": json.dumps([str(fake_server)]),
            "MCP_SERVER_NAME": "test",
            "MCP_LOG_LEVEL": "DEBUG",
            "MCP_RECONNECT_INITIAL": "0.2",
            "MCP_RECONNECT_MAX": "0.5",
            "PYTHONUNBUFFERED": "1",
        })
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ROOT / "mcp_bridge.py"),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(result, 15)
        except Exception:
            await _terminate(proc)
            _stdout, stderr = await proc.communicate()
            self.fail("bridge integration failed: " + stderr.decode("utf-8", "replace")[-4000:])
        finally:
            await _terminate(proc)
            server.close()
            await server.wait_closed()


@unittest.skipIf(websockets is None, "websockets is not installed")
class HttpTransportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _run_case(self, transport: str, remote_path: str) -> None:
        result = asyncio.get_running_loop().create_future()

        async def handler(websocket):
            try:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }))
                init_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert init_response["id"] == 1
                assert init_response["result"]["serverInfo"]["name"] == "fake-fastmcp"

                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "method": "notifications/initialized"
                }))
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
                }))
                tools_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert tools_response["result"]["tools"][0]["name"] == "echo"

                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"text": "hello"}},
                }))
                call_response = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                assert call_response["result"]["content"][0]["text"] == "echo:hello"
                result.set_result(call_response)
            except Exception as exc:  # noqa: BLE001
                if not result.done():
                    result.set_exception(exc)
            finally:
                await websocket.close()

        ws_server = await websockets.serve(handler, "127.0.0.1", 0)
        ws_port = ws_server.sockets[0].getsockname()[1]
        remote_port = _free_port()
        remote = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ROOT / "tests" / "fake_fastmcp_server.py"),
            transport,
            str(remote_port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        bridge = None
        try:
            await asyncio.to_thread(_wait_port, remote_port, 20)
            env = dict(os.environ)
            env.update({
                "MCP_ENDPOINT": f"ws://127.0.0.1:{ws_port}/mcp_endpoint/mcp/?token=test",
                "MCP_TRANSPORT": transport,
                "MCP_URL": f"http://127.0.0.1:{remote_port}{remote_path}",
                "MCP_SERVER_NAME": "test",
                "MCP_LOG_LEVEL": "DEBUG",
                "MCP_TIMEOUT": "10",
                "MCP_SSE_READ_TIMEOUT": "30",
                "MCP_RECONNECT_INITIAL": "0.2",
                "MCP_RECONNECT_MAX": "0.5",
                "PYTHONUNBUFFERED": "1",
            })
            bridge = await asyncio.create_subprocess_exec(
                sys.executable,
                str(ROOT / "mcp_bridge.py"),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(result, 25)
            except Exception:
                await _terminate(bridge)
                _out, bridge_err = await bridge.communicate()
                await _terminate(remote)
                _rout, remote_err = await remote.communicate()
                self.fail(
                    f"{transport} integration failed: "
                    + bridge_err.decode("utf-8", "replace")[-3000:]
                    + "\n--- remote ---\n"
                    + remote_err.decode("utf-8", "replace")[-1500:]
                )
        finally:
            await _terminate(bridge)
            await _terminate(remote)
            ws_server.close()
            await ws_server.wait_closed()

    async def test_sse_end_to_end(self):
        await self._run_case("sse", "/sse")

    async def test_streamable_http_end_to_end(self):
        await self._run_case("streamable-http", "/mcp")


class _NullResultMessage:
    """Stand-in for an upstream message carrying result=null.

    The pinned MCP SDK types JSONRPCResponse.result as a dict, so such a message
    cannot be built with JSONRPCMessage; the wire policy still has to be defined.
    """

    def model_dump_json(self, by_alias: bool = True) -> str:
        return '{"jsonrpc": "2.0", "id": null, "result": null}'


class WirePayloadTests(unittest.TestCase):
    """_dump_message keeps result even when null; only null id/error/params are dropped."""

    def _dump(self, raw):
        message = mcp_bridge.jsonrpc_message_adapter.validate_python(raw)
        return json.loads(mcp_bridge._dump_message(message))

    def test_null_result_is_preserved_and_null_id_dropped(self):
        payload = json.loads(mcp_bridge._dump_message(_NullResultMessage()))
        self.assertIn("result", payload)
        self.assertIsNone(payload["result"])
        self.assertNotIn("id", payload)
        self.assertNotIn("error", payload)

    def test_notification_drops_null_id_and_error(self):
        payload = self._dump({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertNotIn("id", payload)
        self.assertNotIn("error", payload)
        self.assertEqual(payload["method"], "notifications/initialized")

    def test_error_response_keeps_error(self):
        payload = self._dump(
            {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "nope"}}
        )
        self.assertEqual(payload["error"]["code"], -32601)
        self.assertNotIn("result", payload)

    def test_params_null_is_dropped_for_argumentless_request(self):
        payload = self._dump({"jsonrpc": "2.0", "id": 5, "method": "roots/list"})
        self.assertNotIn("params", payload)
        self.assertEqual(payload["method"], "roots/list")
        self.assertEqual(payload["id"], 5)

    def test_params_object_is_kept_intact(self):
        payload = self._dump(
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "echo"}}
        )
        self.assertEqual(payload["params"], {"name": "echo"})

    def test_result_dict_is_kept_intact(self):
        payload = self._dump({"jsonrpc": "2.0", "id": 3, "result": {"ok": True}})
        self.assertEqual(payload["result"], {"ok": True})
        self.assertNotIn("error", payload)

class _FakeWebSocket:
    async def send(self, payload):
        return None


class _FakeConnect:
    async def __aenter__(self):
        return _FakeWebSocket()

    async def __aexit__(self, *_exc):
        return False


class StopTests(unittest.IsolatedAsyncioTestCase):
    """B1: a stop signal must interrupt an active connection promptly."""

    def _config(self):
        return mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_TRANSPORT": "stdio",
            "MCP_COMMAND": "python",
        })

    async def test_run_once_returns_when_stop_event_set(self):
        stop_event = asyncio.Event()

        async def blocking_relay(*_args, **_kwargs):
            await asyncio.sleep(3600)

        @contextlib.asynccontextmanager
        async def fake_transport(_cfg):
            yield object(), object()

        with patch.object(mcp_bridge.websockets, "connect",
                          lambda *_a, **_k: _FakeConnect()), \
                patch.object(mcp_bridge, "open_transport", fake_transport), \
                patch.object(mcp_bridge, "relay_remote_to_ws", blocking_relay), \
                patch.object(mcp_bridge, "relay_ws_to_remote", blocking_relay):
            task = asyncio.create_task(mcp_bridge.run_once(self._config(), stop_event))
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            stop_event.set()
            await asyncio.wait_for(task, 2.0)

    async def test_run_forever_exits_after_stop(self):
        stop_event = asyncio.Event()
        calls = []

        async def fake_run_once(_cfg, _stop_event=None):
            calls.append(time.monotonic())
            await _stop_event.wait()

        with patch.object(mcp_bridge, "run_once", fake_run_once):
            task = asyncio.create_task(
                mcp_bridge.run_forever(self._config(), stop_event)
            )
            await asyncio.sleep(0.05)
            stop_event.set()
            await asyncio.wait_for(task, 2.0)
        self.assertEqual(len(calls), 1)


class _FakeInboundWebSocket:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._payloads:
            raise StopAsyncIteration
        return self._payloads.pop(0)


class _FakeWriteStream:
    def __init__(self):
        self.sent = []

    async def send(self, item):
        self.sent.append(item)


class InboundRelayTests(unittest.IsolatedAsyncioTestCase):
    """B3: a legal response with result=null must reach the MCP server."""

    async def _relay(self, payloads):
        stream = _FakeWriteStream()
        await mcp_bridge.relay_ws_to_remote(
            _FakeInboundWebSocket(payloads), stream, "test"
        )
        return stream

    async def test_null_result_response_is_passed_through(self):
        stream = await self._relay(['{"jsonrpc": "2.0", "id": 1, "result": null}'])
        self.assertEqual(len(stream.sent), 1)
        payload = json.loads(stream.sent[0].message.model_dump_json())
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertEqual(payload["id"], 1)
        self.assertIn("result", payload)
        self.assertIsNone(payload["result"])
        self.assertNotIn("error", payload)

    async def test_valid_request_is_still_parsed_as_sdk_message(self):
        stream = await self._relay(
            ['{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}']
        )
        self.assertEqual(len(stream.sent), 1)
        self.assertIsInstance(stream.sent[0].message, mcp_bridge.JSONRPCMessage)
        payload = json.loads(stream.sent[0].message.model_dump_json(by_alias=True))
        self.assertEqual(payload["method"], "tools/list")

    async def test_invalid_message_is_dropped(self):
        stream = await self._relay([
            '{"jsonrpc": "2.0", "id": 3}',
            "not json",
        ])
        self.assertEqual(stream.sent, [])

    def test_null_result_response_detection(self):
        self.assertTrue(
            mcp_bridge._is_null_result_response(
                {"jsonrpc": "2.0", "id": 1, "result": None}
            )
        )
        self.assertFalse(
            mcp_bridge._is_null_result_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
            )
        )
        self.assertFalse(
            mcp_bridge._is_null_result_response(
                {"jsonrpc": "2.0", "id": 1, "error": None}
            )
        )


class ShellArgsTests(unittest.TestCase):
    def test_windows_style_backslash_path_survives(self):
        with patch.object(mcp_bridge.os, "name", "nt"):
            args = mcp_bridge._split_shell_args(r'C:\tools\server.py --flag')
        self.assertEqual(args, [r"C:\tools\server.py", "--flag"])

    def test_quoted_token_keeps_inner_spaces(self):
        with patch.object(mcp_bridge.os, "name", "nt"):
            args = mcp_bridge._split_shell_args(r'"C:\Program Files\srv.py" --port 1')
        self.assertEqual(args, [r"C:\Program Files\srv.py", "--port", "1"])

    def test_posix_quote_error_is_config_error(self):
        with patch.object(mcp_bridge.os, "name", "posix"):
            with self.assertRaises(mcp_bridge.ConfigError):
                mcp_bridge.BridgeConfig.from_env({
                    "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
                    "MCP_COMMAND": "python",
                    "MCP_ARGS": 'server.py "--oops',
                })


class EnvIsolationTests(unittest.TestCase):
    def test_supplied_env_mapping_is_used_verbatim(self):
        source = {
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
            "MCP_ENV": '{"FOO":"bar"}',
            "PATH": "/usr/bin",
        }
        cfg = mcp_bridge.BridgeConfig.from_env(source)
        self.assertEqual(cfg.env["FOO"], "bar")
        self.assertEqual(cfg.env["PATH"], "/usr/bin")
        # Nothing beyond the supplied mapping plus MCP_ENV overrides leaks in;
        # a host-env copy would drag in far more keys than the four below.
        self.assertEqual(set(cfg.env), set(source) | {"FOO"})


class _FakeProc:
    """Stands in for /proc without touching the filesystem."""

    def __init__(self, mapping):
        self.mapping = mapping

    def listdir(self, root):
        return list(self.mapping) + ["self"]

    def open(self, path, mode="rb"):
        pid = str(path).replace("\\", "/").split("/")[-2]
        if pid not in self.mapping:
            raise FileNotFoundError(path)
        return io.BytesIO(self.mapping[pid])


class HealthcheckTests(unittest.TestCase):
    def test_unreadable_proc_root_reports_unhealthy(self):
        def boom(root):
            raise OSError("no /proc here")

        with patch.object(mcp_bridge_healthcheck.os, "listdir", boom):
            self.assertFalse(mcp_bridge_healthcheck.bridge_running())

    def test_bridge_process_is_detected(self):
        proc = _FakeProc({"4242": b"python\x00-u\x00mcp_bridge.py\x00"})
        with patch.object(mcp_bridge_healthcheck.os, "listdir", proc.listdir), patch(
            "builtins.open", proc.open
        ):
            self.assertTrue(mcp_bridge_healthcheck.bridge_running("/proc"))

    def test_unrelated_process_is_not_health(self):
        proc = _FakeProc({"7": b"python\x00-c\x00pass\x00"})
        with patch.object(mcp_bridge_healthcheck.os, "listdir", proc.listdir), patch(
            "builtins.open", proc.open
        ):
            self.assertFalse(mcp_bridge_healthcheck.bridge_running("/proc"))


class ReconnectBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_backoff_doubles_then_caps_at_reconnect_max(self):
        stop = asyncio.Event()
        waits: list[float] = []

        async def fake_run_once(cfg, stop_event=None):
            raise RuntimeError("connection refused")

        async def fake_wait_for(awaitable, timeout):
            waits.append(timeout)
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            if len(waits) >= 3:
                stop.set()
            raise asyncio.TimeoutError

        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
            "MCP_RECONNECT_INITIAL": "0.1",
            "MCP_RECONNECT_MAX": "0.25",
        })
        real_wait_for = asyncio.wait_for
        with patch.object(mcp_bridge, "run_once", fake_run_once), patch.object(
            mcp_bridge.asyncio, "wait_for", fake_wait_for
        ):
            await real_wait_for(mcp_bridge.run_forever(cfg, stop), timeout=5)

        self.assertEqual(waits[:3], [0.1, 0.2, 0.25])


class TestDoubleGuardTests(unittest.TestCase):
    def test_fake_fastmcp_rejects_missing_arguments(self):
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import fake_fastmcp_server
        finally:
            sys.path.pop(0)
        self.assertEqual(fake_fastmcp_server.main([]), 2)
        self.assertEqual(fake_fastmcp_server.main(["stdio", "not-a-port"]), 2)


class VersionLoggingTests(unittest.TestCase):
    def test_runtime_versions_reports_bridge_and_dependencies(self):
        versions = mcp_bridge.runtime_versions()
        self.assertEqual(versions["bridge"], mcp_bridge.__version__)
        for name in ("mcp", "httpx2", "websockets"):
            self.assertIn(name, versions)
            self.assertTrue(versions[name])

    def test_startup_log_carries_version(self):
        env = {
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
        }

        async def fake_run_forever(cfg, stop_event):
            return None

        with patch.dict(os.environ, env, clear=True), patch.object(
            mcp_bridge, "run_forever", fake_run_forever
        ), self.assertLogs("mcp-bridge", level="INFO") as captured:
            self.assertEqual(mcp_bridge.main([]), 0)
        joined = "\n".join(captured.output)
        self.assertIn(f"mcp-bridge {mcp_bridge.__version__}", joined)
        self.assertIn("mcp ", joined)

    def test_print_config_includes_version(self):
        cfg = mcp_bridge.BridgeConfig.from_env({
            "MCP_ENDPOINT": "ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=abc",
            "MCP_COMMAND": "python",
        })
        self.assertEqual(
            mcp_bridge._public_config(cfg)["version"], mcp_bridge.__version__
        )


if __name__ == "__main__":
    unittest.main()
