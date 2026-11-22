"""WebSocket handler behavior tests."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from src.handlers import websocket_handler as handler_module
from src.utils.jsonrpc import JSONRPCProtocol


class RobotHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_object_json_returns_invalid_request(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_robot_message("agent", "[1, 2]", "robot")
        sent = forward.await_args.args[1]
        self.assertEqual(sent["error"]["code"], JSONRPCProtocol.INVALID_REQUEST)

    async def test_tool_call_without_name_returns_invalid_params(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_tool_call_request(
                {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}},
                "agent", "robot", 7,
            )
        sent = forward.await_args.args[1]
        self.assertEqual(sent["error"]["code"], JSONRPCProtocol.INVALID_PARAMS)
        self.assertEqual(sent["id"], 7)

    async def test_tool_call_unknown_tool_returns_method_not_found(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "find_tool_connection", return_value=None
        ), patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_tool_call_request(
                {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "missing"}},
                "agent", "robot", 8,
            )
        sent = forward.await_args.args[1]
        self.assertEqual(sent["error"]["code"], JSONRPCProtocol.METHOD_NOT_FOUND)
        self.assertIn("missing", sent["error"]["message"])


    async def test_notification_without_id_gets_no_reply_when_no_mcp(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "get_agent_mcp_connections", return_value=[]
        ), patch.object(
            handler_module.connection_manager,
            "forward_to_robot_by_uuid",
            AsyncMock(return_value=True),
        ) as forward:
            await handler._handle_robot_message(
                "agent",
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                "robot",
            )
        forward.assert_not_awaited()


class ServerMessageHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_server_json_is_ignored(self):
        handler = handler_module.WebSocketHandler()
        await handler._handle_mcp_server_message("agent", "uuid", "not json")

    async def test_non_object_server_json_is_ignored(self):
        handler = handler_module.WebSocketHandler()
        await handler._handle_mcp_server_message("agent", "uuid", json.dumps([1, 2]))


if __name__ == "__main__":
    unittest.main()
