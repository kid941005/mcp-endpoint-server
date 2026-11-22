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
        self.auth_headers = {"Authorization": f"Bearer {self.key}"}

    def test_root_reports_running(self):
        response = self.client.get("/mcp_endpoint/")
        body = response.json()
        self.assertEqual(body["result"]["status"], "running")
        self.assertEqual(body["result"]["message"], "MCP Endpoint Server")

    def test_health_requires_key(self):
        body = self.client.get("/mcp_endpoint/health").json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_health_accepts_bearer_header(self):
        body = self.client.get("/mcp_endpoint/health", headers=self.auth_headers).json()
        self.assertEqual(body["result"]["status"], "success")

    def test_health_accepts_raw_key_header(self):
        body = self.client.get(
            "/mcp_endpoint/health", headers={"Authorization": self.key}
        ).json()
        self.assertEqual(body["result"]["status"], "success")

    def test_health_rejects_wrong_header_key(self):
        body = self.client.get(
            "/mcp_endpoint/health", headers={"Authorization": "Bearer wrong-key"}
        ).json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_header_takes_precedence_over_query(self):
        body = self.client.get(
            "/mcp_endpoint/health",
            params={"key": self.key},
            headers={"Authorization": "Bearer wrong-key"},
        ).json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_health_still_accepts_query_key_for_compat(self):
        body = self.client.get("/mcp_endpoint/health", params={"key": self.key}).json()
        self.assertEqual(body["result"]["status"], "success")

    def test_stats_requires_key(self):
        body = self.client.get("/mcp_endpoint/stats").json()
        self.assertEqual(body["error"]["code"], -32004)

    def test_stats_returns_connection_shape(self):
        body = self.client.get("/mcp_endpoint/stats", headers=self.auth_headers).json()
        result = body["result"]
        self.assertIn("mcp_server_connections", result)
        self.assertIn("robot_connections", result)
        self.assertIn("total_tools", result)

    def test_stats_still_accepts_query_key_for_compat(self):
        body = self.client.get("/mcp_endpoint/stats", params={"key": self.key}).json()
        self.assertIn("mcp_server_connections", body["result"])


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
