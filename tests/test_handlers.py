"""WebSocket handler behavior tests."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.core.connection_manager import PendingResponse
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


    async def test_tool_call_notification_without_id_gets_no_reply(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_tool_call_request(
                {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "x"}},
                "agent", "robot", None,
            )
        forward.assert_not_awaited()

    async def test_tool_call_with_non_object_params_returns_invalid_params(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_tool_call_request(
                {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": [1, 2]},
                "agent", "robot", 11,
            )
        sent = forward.await_args.args[1]
        self.assertEqual(sent["error"]["code"], JSONRPCProtocol.INVALID_PARAMS)
        self.assertEqual(sent["id"], 11)


class ServerMessageHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_server_json_is_ignored(self):
        handler = handler_module.WebSocketHandler()
        before = dict(handler_module.connection_manager.pending_responses)
        self.assertIsNone(await handler._handle_mcp_server_message("agent", "uuid", "not json"))
        self.assertEqual(handler_module.connection_manager.pending_responses, before)

    async def test_non_object_server_json_is_ignored(self):
        handler = handler_module.WebSocketHandler()
        before = dict(handler_module.connection_manager.pending_responses)
        self.assertIsNone(await handler._handle_mcp_server_message("agent", "uuid", json.dumps([1, 2])))
        self.assertEqual(handler_module.connection_manager.pending_responses, before)

    async def test_server_request_is_forwarded_to_robot(self):
        """服务器主动请求（sampling/createMessage）应转发给小智端。"""
        handler = handler_module.WebSocketHandler()
        robot = SimpleNamespace(connection_uuid="robot-1", agent_id="agent")
        message = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "sampling/createMessage",
            "params": {"maxTokens": 16},
        }
        with patch.object(
            handler_module.connection_manager,
            "get_robot_connections_by_agent",
            return_value=[robot],
        ), patch.object(
            handler_module.connection_manager,
            "forward_to_robot_by_uuid",
            AsyncMock(return_value=True),
        ) as forward:
            await handler._handle_mcp_server_message("agent", "uuid-1", json.dumps(message))
        forward.assert_awaited_once()
        self.assertEqual(forward.await_args.args[0], "robot-1")
        self.assertEqual(forward.await_args.args[1], message)

    async def test_server_notification_is_forwarded_to_robot(self):
        """notifications/tools/list_changed 转发给小智端，触发工具列表热更新。"""
        handler = handler_module.WebSocketHandler()
        robot = SimpleNamespace(connection_uuid="robot-2", agent_id="agent")
        message = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        with patch.object(
            handler_module.connection_manager,
            "get_robot_connections_by_agent",
            return_value=[robot],
        ), patch.object(
            handler_module.connection_manager,
            "forward_to_robot_by_uuid",
            AsyncMock(return_value=True),
        ) as forward:
            await handler._handle_mcp_server_message("agent", "uuid-2", json.dumps(message))
        forward.assert_awaited_once()
        self.assertEqual(forward.await_args.args[1], message)

    async def test_response_with_unknown_id_is_not_forwarded(self):
        """未知 id 的响应不是通知，不应转发给小智端。"""
        handler = handler_module.WebSocketHandler()
        robot = SimpleNamespace(connection_uuid="robot-3", agent_id="agent")
        message = {"jsonrpc": "2.0", "id": 1001, "result": {"ok": True}}
        with patch.object(
            handler_module.connection_manager,
            "get_robot_connections_by_agent",
            return_value=[robot],
        ), patch.object(
            handler_module.connection_manager,
            "forward_to_robot_by_uuid",
            AsyncMock(return_value=True),
        ) as forward:
            await handler._handle_mcp_server_message("agent", "uuid-3", json.dumps(message))
        forward.assert_not_awaited()

    async def test_server_message_is_dropped_when_no_robot(self):
        """没有小智端连接时不报错，仅丢弃。"""
        handler = handler_module.WebSocketHandler()
        message = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
        with patch.object(
            handler_module.connection_manager,
            "get_robot_connections_by_agent",
            return_value=[],
        ), patch.object(
            handler_module.connection_manager,
            "forward_to_robot_by_uuid",
            AsyncMock(return_value=True),
        ) as forward:
            await handler._handle_mcp_server_message("agent", "uuid-4", json.dumps(message))
        forward.assert_not_awaited()

    async def test_tools_list_without_pending_request_is_ignored(self):
        """未登记的 tools/list 形状响应不能覆盖工具注册表。"""
        handler = handler_module.WebSocketHandler()
        message = {
            "jsonrpc": "2.0",
            "id": "forged_n_1",
            "result": {"tools": [{"name": "evil"}]},
        }
        with patch.object(
            handler_module.connection_manager, "update_tool_list"
        ) as update:
            await handler._handle_mcp_server_message("agent", "uuid-9", json.dumps(message))
        update.assert_not_called()

    async def test_tools_list_with_pending_request_updates_registry(self):
        handler = handler_module.WebSocketHandler()
        manager = handler_module.connection_manager
        pending = PendingResponse("uuid-9_n_1", 1, "uuid-9", ["uuid-9"], "tools/list")
        manager.pending_responses["uuid-9_n_1"] = pending
        message = {
            "jsonrpc": "2.0",
            "id": "uuid-9_n_1",
            "result": {"tools": [{"name": "echo"}]},
        }
        try:
            with patch.object(manager, "update_tool_list") as update, patch.object(
                manager, "add_server_response", return_value=None
            ):
                await handler._handle_mcp_server_message(
                    "agent", "uuid-9", json.dumps(message)
                )
        finally:
            manager.pending_responses.pop("uuid-9_n_1", None)
        update.assert_called_once_with("agent", "uuid-9", [{"name": "echo"}])


if __name__ == "__main__":
    unittest.main()
