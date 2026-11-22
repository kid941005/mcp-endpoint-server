"""End-to-end WebSocket relay tests for the endpoint server."""
import concurrent.futures
import json
import unittest
import urllib.parse
from contextlib import contextmanager

from fastapi.testclient import TestClient

from src.core.connection_manager import connection_manager
from src.server import app
from src.utils.aes_utils import encrypt
from src.utils.config import config


class WebSocketRelayTests(unittest.TestCase):
    def _token(self):
        plain = json.dumps({"agentId": "agent-e2e"})
        return urllib.parse.quote(encrypt(config.get("server", "key"), plain), safe="")

    def test_initialize_tools_list_and_tool_call(self):
        token = self._token()
        with TestClient(app) as client:
            with client.websocket_connect("/mcp_endpoint/mcp/?token=" + token) as tool, \
                    client.websocket_connect("/mcp_endpoint/call/?token=" + token) as robot:
                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {},
                }))
                forwarded = json.loads(tool.receive_text())
                self.assertNotEqual(forwarded["id"], 0)
                tool.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": forwarded["id"],
                    "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
                }))
                reply = json.loads(robot.receive_text())
                self.assertEqual(reply["id"], 0)
                self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")

                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
                }))
                forwarded = json.loads(tool.receive_text())
                tool.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": forwarded["id"],
                    "result": {"tools": [{"name": "echo", "description": "e", "inputSchema": {"type": "object"}}]},
                }))
                reply = json.loads(robot.receive_text())
                self.assertEqual(reply["id"], 1)
                self.assertEqual(len(reply["result"]["tools"]), 1)

                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"x": 1}},
                }))
                forwarded = json.loads(tool.receive_text())
                tool.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": forwarded["id"],
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                }))
                reply = json.loads(robot.receive_text())
                self.assertEqual(reply["id"], 2)
                self.assertEqual(reply["result"]["content"][0]["text"], "ok")


class DualToolServerRelayTests(unittest.TestCase):
    """两个 MCP 工具端同时接入时的端到端行为。"""

    TOOLS_A = [
        {"name": "shared", "description": "from-a", "inputSchema": {"type": "object"}},
        {"name": "only-a", "description": "a", "inputSchema": {"type": "object"}},
    ]
    TOOLS_B = [
        {"name": "shared", "description": "from-b", "inputSchema": {"type": "object"}},
        {"name": "only-b", "description": "b", "inputSchema": {"type": "object"}},
    ]

    def _token(self, agent_id: str) -> str:
        plain = json.dumps({"agentId": agent_id})
        return urllib.parse.quote(encrypt(config.get("server", "key"), plain), safe="")

    @contextmanager
    def _session(self, client, agent_id):
        """建立两个工具端 + 一个小智端，三个连接共用同一个 agentId。"""
        token = self._token(agent_id)
        with client.websocket_connect("/mcp_endpoint/mcp/?token=" + token) as tool_a, \
                client.websocket_connect("/mcp_endpoint/mcp/?token=" + token) as tool_b, \
                client.websocket_connect("/mcp_endpoint/call/?token=" + token) as robot:
            yield tool_a, tool_b, robot

    @staticmethod
    def _recv(socket_, timeout: float = 10.0):
        """带超时的接收：路由打错连接时快速失败而不是挂死整个用例。"""
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            payload = pool.submit(socket_.receive_text).result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise AssertionError("等待消息超时，请求可能被路由到了非预期连接")
        finally:
            pool.shutdown(wait=False)
        return json.loads(payload)

    def _register_tools(self, robot, tool_a, tool_b, request_id):
        """广播 tools/list 并让两个工具端各自回包，返回小智端收到的聚合响应。"""
        robot.send_text(json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {},
        }))
        for socket_, tools in ((tool_a, self.TOOLS_A), (tool_b, self.TOOLS_B)):
            forwarded = self._recv(socket_)
            socket_.send_text(json.dumps({
                "jsonrpc": "2.0", "id": forwarded["id"], "result": {"tools": tools},
            }))
        return self._recv(robot)

    def test_initialize_returns_single_mcp_result(self):
        with TestClient(app) as client:
            with self._session(client, "agent-dual-init") as (tool_a, tool_b, robot):
                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {},
                }))
                for socket_ in (tool_a, tool_b):
                    forwarded = self._recv(socket_)
                    self.assertEqual(forwarded["method"], "initialize")
                    socket_.send_text(json.dumps({
                        "jsonrpc": "2.0", "id": forwarded["id"],
                        "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
                    }))
                reply = self._recv(robot)
                self.assertEqual(reply["id"], 0)
                # 多工具端不得把 initialize 结果包装成 {"responses": [...]}
                self.assertNotIn("responses", reply["result"])
                self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")

    def test_tools_list_dedups_shared_tool_across_servers(self):
        agent_id = "agent-dual-list"
        with TestClient(app) as client:
            with self._session(client, agent_id) as (tool_a, tool_b, robot):
                reply = self._register_tools(robot, tool_a, tool_b, 1)
                self.assertEqual(reply["id"], 1)
                tools = reply["result"]["tools"]
                self.assertEqual([tool["name"] for tool in tools], ["only-a", "only-b", "shared"])

                servers = connection_manager.mcp_server_connections[agent_id]
                owners = sorted(
                    uuid_ for uuid_, conn in servers.items() if "shared" in conn.tools
                )
                self.assertEqual(len(owners), 2)
                shared = tools[2]
                self.assertEqual(
                    shared["_meta"]["mcp-endpoint-server"]["shadowed_by"], owners[1:]
                )
                winner = servers[owners[0]]
                self.assertEqual(
                    shared["description"], "from-a" if "only-a" in winner.tools else "from-b"
                )

    def test_tool_call_routes_to_owning_server(self):
        agent_id = "agent-dual-call"
        with TestClient(app) as client:
            with self._session(client, agent_id) as (tool_a, tool_b, robot):
                self._register_tools(robot, tool_a, tool_b, 1)

                # 独占工具名只能打到唯一持有它的工具端
                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "only-b", "arguments": {}},
                }))
                forwarded = self._recv(tool_b)
                self.assertEqual(forwarded["params"]["name"], "only-b")
                tool_b.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": forwarded["id"],
                    "result": {"content": [{"type": "text", "text": "b-ok"}]},
                }))
                reply = self._recv(robot)
                self.assertEqual(reply["id"], 2)
                self.assertEqual(reply["result"]["content"][0]["text"], "b-ok")

                # 同名工具必须与 tools/list 展示的服务器一致（UUID 最小者）
                servers = connection_manager.mcp_server_connections[agent_id]
                owners = sorted(
                    uuid_ for uuid_, conn in servers.items() if "shared" in conn.tools
                )
                expected = tool_a if "only-a" in servers[owners[0]].tools else tool_b
                robot.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "shared", "arguments": {}},
                }))
                forwarded = self._recv(expected)
                self.assertEqual(forwarded["params"]["name"], "shared")
                expected.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": forwarded["id"],
                    "result": {"content": [{"type": "text", "text": "shared-ok"}]},
                }))
                reply = self._recv(robot)
                self.assertEqual(reply["id"], 3)
                self.assertEqual(reply["result"]["content"][0]["text"], "shared-ok")


if __name__ == "__main__":
    unittest.main()
