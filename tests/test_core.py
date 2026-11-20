"""Core protocol and connection-manager regression tests."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.core.connection_manager import ConnectionManager, PendingResponse
from src.handlers import websocket_handler as handler_module
from src.utils.jsonrpc import JSONRPCProtocol


class IdHandlingTests(unittest.TestCase):
    def test_zero_id_is_transformed_and_restored(self):
        manager = ConnectionManager()
        message = manager.transform_jsonrpc_message({"jsonrpc": "2.0", "id": 0, "method": "x"}, "uuid")
        self.assertNotEqual(message["id"], 0)
        self.assertEqual(message["id"], "uuid_n_0")
        connection_uuid, restored = manager.restore_jsonrpc_message(message)
        self.assertEqual(connection_uuid, "uuid")
        self.assertEqual(restored["id"], 0)

    def test_non_string_transformed_id_is_ignored(self):
        manager = ConnectionManager()
        connection_uuid, restored = manager.restore_jsonrpc_message({"id": 123})
        self.assertIsNone(connection_uuid)
        self.assertEqual(restored["id"], 123)

    def test_notification_without_id_is_not_transformed(self):
        manager = ConnectionManager()
        message = manager.transform_jsonrpc_message({"jsonrpc": "2.0", "method": "notify"}, "uuid")
        self.assertNotIn("id", message)


class AggregateTests(unittest.TestCase):
    def _pending(self, responses):
        pending = PendingResponse("uuid_n_1", 1, "uuid", ["a", "b"])
        pending.received_responses = responses
        return pending

    def test_error_response_is_not_mixed_into_tools(self):
        pending = self._pending({
            "a": {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"tools": [{"name": "x"}]}},
            "b": {"jsonrpc": "2.0", "id": "uuid_n_1", "error": {"code": -32603, "message": "boom"}},
        })
        result = ConnectionManager().aggregate_responses(pending)
        self.assertEqual(result["error"]["message"], "boom")
        self.assertNotIn("result", result)

    def test_tools_are_merged_without_extra_result_keys(self):
        pending = self._pending({
            "a": {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"tools": [{"name": "x"}]}},
            "b": {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"tools": [{"name": "y"}]}},
        })
        result = ConnectionManager().aggregate_responses(pending)
        self.assertEqual(result["id"], "uuid_n_1")
        self.assertEqual([tool["name"] for tool in result["result"]["tools"]], ["x", "y"])
        self.assertEqual(set(result["result"]), {"tools"})


class PendingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_response_times_out_and_reports_error(self):
        manager = ConnectionManager()
        manager.pending_timeout = 0.01
        manager.forward_to_robot_by_uuid = AsyncMock(return_value=True)
        manager.register_pending_response("uuid_n_1", 1, "robot", ["mcp"])
        await asyncio.sleep(0.05)
        self.assertNotIn("uuid_n_1", manager.pending_responses)
        manager.forward_to_robot_by_uuid.assert_awaited_once()
        sent = manager.forward_to_robot_by_uuid.await_args.args[1]
        self.assertEqual(sent["id"], 1)
        self.assertEqual(sent["error"]["code"], -32002)

    async def test_discard_failed_connection_reduces_expected_set(self):
        manager = ConnectionManager()
        manager.register_pending_response("uuid_n_1", 1, "robot", ["a", "b"])
        manager.add_server_response(
            "uuid_n_1", "a", {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"tools": []}}
        )
        pending = manager.discard_expected_connection("uuid_n_1", "b")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.expected_connections, {"a"})
        manager.remove_pending_response("uuid_n_1")

    async def test_send_failure_unregisters_connection(self):
        manager = ConnectionManager()

        async def send(_message):
            raise RuntimeError("closed")

        connection = SimpleNamespace(websocket=SimpleNamespace(send_text=send), agent_id="agent")
        manager.mcp_server_connections = {"agent": {"connection": connection}}
        self.assertFalse(await manager.forward_to_mcp_server("agent", "connection", {"id": 1}))
        self.assertNotIn("agent", manager.mcp_server_connections)


class JSONRPCShapeTests(unittest.TestCase):
    def test_success_response_has_no_error_key(self):
        data = JSONRPCProtocol.to_dict(JSONRPCProtocol.create_success_response({"ok": True}, 1))
        self.assertEqual(set(data), {"jsonrpc", "id", "result"})

    def test_error_response_has_no_result_key(self):
        data = JSONRPCProtocol.to_dict(
            JSONRPCProtocol.create_error_response(-32603, "boom", request_id=1)
        )
        self.assertEqual(set(data), {"jsonrpc", "id", "error"})
        self.assertNotIn("data", data["error"])


class RobotParseTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_json_returns_parse_error(self):
        handler = handler_module.WebSocketHandler()
        with patch.object(
            handler_module.connection_manager, "forward_to_robot_by_uuid", AsyncMock(return_value=True)
        ) as forward:
            await handler._handle_robot_message("agent", "not json", "robot")
        forward.assert_awaited_once()
        sent = forward.await_args.args[1]
        self.assertEqual(sent["error"]["code"], JSONRPCProtocol.PARSE_ERROR)
        self.assertNotIn("result", sent)


if __name__ == "__main__":
    unittest.main()
