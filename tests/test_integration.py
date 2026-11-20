"""End-to-end WebSocket relay tests for the endpoint server."""
import json
import unittest
import urllib.parse

from fastapi.testclient import TestClient

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


if __name__ == "__main__":
    unittest.main()
