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

    def test_string_id_with_underscores_roundtrips(self):
        manager = ConnectionManager()
        message = manager.transform_jsonrpc_message({"id": "abc_def"}, "uuid")
        connection_uuid, restored = manager.restore_jsonrpc_message(message)
        self.assertEqual(connection_uuid, "uuid")
        self.assertEqual(restored["id"], "abc_def")


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

    def test_multiple_non_tools_responses_keep_single_result(self):
        pending = self._pending({
            "a": {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"protocolVersion": "2024-11-05"}},
            "b": {"jsonrpc": "2.0", "id": "uuid_n_1", "result": {"protocolVersion": "2025-03-26"}},
        })
        result = ConnectionManager().aggregate_responses(pending)
        self.assertEqual(result["result"]["protocolVersion"], "2024-11-05")
        self.assertNotIn("responses", result["result"])

    def test_empty_responses_return_internal_error(self):
        pending = self._pending({})
        result = ConnectionManager().aggregate_responses(pending)
        self.assertEqual(result["error"]["code"], -32603)
        self.assertNotIn("result", result)


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

    async def test_reregister_cancels_previous_timeout_task(self):
        manager = ConnectionManager()
        manager.pending_timeout = 0.05
        manager.forward_to_robot_by_uuid = AsyncMock(return_value=True)
        manager.register_pending_response("uuid_n_1", 1, "robot", ["mcp"])
        first = manager.pending_responses["uuid_n_1"]
        manager.register_pending_response("uuid_n_1", 2, "robot", ["mcp"])
        await asyncio.sleep(0.01)
        self.assertTrue(first.timeout_task.done())
        self.assertEqual(manager.pending_responses["uuid_n_1"].request_id, 2)
        manager.forward_to_robot_by_uuid.assert_not_awaited()
        manager.remove_pending_response("uuid_n_1")

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


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_tools_stats_and_unregister(self):
        manager = ConnectionManager()
        websocket = SimpleNamespace(send_text=AsyncMock())
        mcp_uuid = await manager.register_mcp_server_connection("agent", websocket)
        manager.update_tool_list("agent", mcp_uuid, [{"name": "echo", "description": "e"}])
        manager.update_server_info("agent", mcp_uuid, {"name": "fake"})

        self.assertEqual(manager.find_tool_connection("agent", "echo"), mcp_uuid)
        self.assertTrue(manager.is_mcp_server_connected("agent", mcp_uuid))
        self.assertEqual(manager.get_agent_mcp_connections("agent"), [mcp_uuid])
        self.assertEqual(manager.get_available_agents(), ["agent"])
        self.assertEqual([tool["name"] for tool in manager.get_all_tools("agent")], ["echo"])
        self.assertEqual(manager.get_agent_tools_summary("agent")["tools"][0]["name"], "echo")

        robot_uuid = await manager.register_robot_connection("agent", websocket)
        self.assertTrue(manager.is_robot_connected("agent"))
        self.assertEqual(len(manager.get_robot_connections_by_agent("agent")), 1)
        stats = manager.get_connection_stats()
        self.assertEqual(stats["mcp_server_connections"], 1)
        self.assertEqual(stats["robot_connections"], 1)
        self.assertEqual(stats["total_tools"], 1)

        await manager.unregister_robot_connection(robot_uuid)
        await manager.unregister_mcp_server_connection("agent", mcp_uuid)
        self.assertFalse(manager.is_robot_connected("agent"))
        self.assertEqual(manager.get_available_agents(), [])


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


class ToolConflictTests(unittest.IsolatedAsyncioTestCase):
    async def _manager_with_conflict(self, low_first: bool):
        manager = ConnectionManager()
        websocket = SimpleNamespace(send_text=AsyncMock())
        first = await manager.register_mcp_server_connection("agent", websocket)
        second = await manager.register_mcp_server_connection("agent", websocket)
        low, high = sorted([first, second])
        order = [(low, "low"), (high, "high")] if low_first else [(high, "high"), (low, "low")]
        for connection_uuid, label in order:
            manager.update_tool_list(
                "agent", connection_uuid, [{"name": "echo", "description": label}]
            )
        return manager, low, high

    async def test_list_and_call_pick_same_connection(self):
        manager, low, high = await self._manager_with_conflict(low_first=False)
        tools = manager.get_all_tools("agent")
        self.assertEqual([tool["name"] for tool in tools], ["echo"])
        self.assertEqual(tools[0]["description"], "low")
        self.assertEqual(tools[0]["_meta"]["mcp-endpoint-server"]["shadowed_by"], [high])
        self.assertEqual(manager.find_tool_connection("agent", "echo"), low)
        summary = manager.get_agent_tools_summary("agent")
        self.assertEqual([tool["description"] for tool in summary["tools"]], ["low"])

    async def test_registration_order_does_not_change_selection(self):
        manager, low, _ = await self._manager_with_conflict(low_first=True)
        self.assertEqual(manager.get_all_tools("agent")[0]["description"], "low")
        self.assertEqual(
            manager.get_agent_tools_summary("agent")["tools"][0]["description"], "low"
        )
        self.assertEqual(manager.find_tool_connection("agent", "echo"), low)

    async def test_no_conflict_keeps_tool_untouched(self):
        manager = ConnectionManager()
        websocket = SimpleNamespace(send_text=AsyncMock())
        connection_uuid = await manager.register_mcp_server_connection("agent", websocket)
        manager.update_tool_list("agent", connection_uuid, [{"name": "echo"}])
        self.assertNotIn("_meta", manager.get_all_tools("agent")[0])
        self.assertNotIn("_meta", manager.get_agent_tools_summary("agent")["tools"][0])

    async def test_stats_reports_tool_conflicts(self):
        manager, low, high = await self._manager_with_conflict(low_first=False)
        stats = manager.get_connection_stats()
        self.assertEqual(stats["total_tools"], 2)
        self.assertEqual(
            stats["tool_conflicts"],
            [{"agent_id": "agent", "tool": "echo", "uuids": [low, high]}],
        )


if __name__ == "__main__":
    unittest.main()
