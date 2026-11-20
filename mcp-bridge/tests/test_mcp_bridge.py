from __future__ import annotations

import asyncio
import contextlib
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
        self.assertEqual(calls[0][1]["headers"], {"X-Test": "1"})


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


if __name__ == "__main__":
    unittest.main()
