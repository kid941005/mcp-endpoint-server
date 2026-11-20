"""HTTP and WebSocket endpoint tests."""
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.server import app
from src.utils.config import config


class HttpEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.key = config.get("server", "key")

    def test_root_reports_running(self):
        response = self.client.get("/mcp_endpoint/")
        body = response.json()
        self.assertEqual(body["result"]["status"], "running")
        self.assertEqual(body["result"]["message"], "MCP Endpoint Server")

    def test_health_requires_key(self):
        body = self.client.get("/mcp_endpoint/health").json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_health_accepts_valid_key(self):
        body = self.client.get("/mcp_endpoint/health", params={"key": self.key}).json()
        self.assertEqual(body["result"]["status"], "success")

    def test_stats_requires_key(self):
        body = self.client.get("/mcp_endpoint/stats").json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_stats_returns_connection_shape(self):
        body = self.client.get("/mcp_endpoint/stats", params={"key": self.key}).json()
        result = body["result"]
        self.assertIn("mcp_server_connections", result)
        self.assertIn("robot_connections", result)
        self.assertIn("total_tools", result)


class WebSocketAuthTests(unittest.TestCase):
    def test_missing_token_is_rejected(self):
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/mcp_endpoint/call/") as websocket:
                    websocket.receive_text()
        self.assertEqual(ctx.exception.code, 1008)

    def test_invalid_token_is_rejected(self):
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/mcp_endpoint/call/?token=bad") as websocket:
                    websocket.receive_text()
        self.assertEqual(ctx.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()
