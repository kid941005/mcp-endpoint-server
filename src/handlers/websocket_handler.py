"""
WebSocket处理器
处理工具端和小智端的WebSocket连接
"""

import json
from ..core.connection_manager import connection_manager
from ..utils.logger import get_logger
from ..utils.jsonrpc import (
    create_tool_not_connected_error,
    create_forward_failed_error,
    JSONRPCProtocol,
)

logger = get_logger()


class WebSocketHandler:
    """WebSocket处理器"""

    async def _reply_error(
        self,
        connection_uuid: str,
        request_id,
        error_code: int,
        error_message: str,
        error_data=None,
    ) -> None:
        """构造并回送 JSON-RPC 错误响应（统一错误出口）。"""
        response = JSONRPCProtocol.create_error_response(
            error_code=error_code,
            error_message=error_message,
            error_data=error_data,
            request_id=request_id,
        )
        await connection_manager.forward_to_robot_by_uuid(
            connection_uuid, JSONRPCProtocol.to_dict(response)
        )

    async def _handle_mcp_server_message(self, agent_id: str, mcp_connection_uuid: str, message: str):
        """处理MCP服务器消息"""
        logger.debug(f"收到MCP服务器消息: {agent_id} (UUID: {mcp_connection_uuid}) - {message}")

        try:
            message_data = json.loads(message)
        except json.JSONDecodeError:
            logger.error(f"MCP服务器消息不是有效的 JSON，已忽略: {message}")
            return

        if not isinstance(message_data, dict):
            logger.error(f"MCP服务器消息不是 JSON 对象，已忽略: {message}")
            return

        try:
            logger.debug(f"---------MCP服务器消息---------: {message_data}")

            if self._is_tools_list_response(message_data):
                logger.debug(f"收到工具列表响应: {agent_id} (UUID: {mcp_connection_uuid})")
                self._handle_tools_list_response(agent_id, mcp_connection_uuid, message_data)

            if self._is_initialize_response(message_data):
                logger.debug(f"收到初始化响应: {agent_id} (UUID: {mcp_connection_uuid})")
                self._handle_initialize_response(agent_id, mcp_connection_uuid, message_data)

            transformed_id = message_data.get("id")
            if transformed_id is not None and transformed_id in connection_manager.pending_responses:
                pending = connection_manager.add_server_response(
                    transformed_id, mcp_connection_uuid, message_data
                )
                if pending:
                    logger.debug("---------所有期望服务器已响应，开始聚合---------")
                    await self._forward_aggregated_response(pending, transformed_id)

            if self._is_server_initiated_message(message_data):
                logger.debug(
                    f"收到服务器主动消息: {agent_id} (UUID: {mcp_connection_uuid}) - "
                    f"{message_data.get('method')}"
                )
                await self._forward_to_robot_agents(
                    agent_id, mcp_connection_uuid, message_data
                )

        except Exception as e:
            logger.exception(f"处理MCP服务器消息时发生错误: {e}")

    def _is_server_initiated_message(self, message_data: dict) -> bool:
        """判断是否为 MCP 服务器主动发起的请求/通知（而非对客户端请求的响应）。

        服务器可以主动请求客户端能力（sampling/createMessage、roots/list、
        elicitation/create）或推送通知（notifications/tools/list_changed、
        notifications/message）。它们没有登记在 pending_responses 中，若不转发
        给小智端，请求永远得不到响应，工具列表热更新也会失效。
        """
        if "method" not in message_data:
            return False
        message_id = message_data.get("id")
        if message_id is None:
            return True
        return message_id not in connection_manager.pending_responses

    async def _forward_to_robot_agents(
        self, agent_id: str, mcp_connection_uuid: str, message_data: dict
    ) -> None:
        """把 MCP 服务器主动消息原样转发给该 agent 的所有小智端连接。"""
        robot_connections = connection_manager.get_robot_connections_by_agent(agent_id)
        if not robot_connections:
            logger.debug(
                f"agent {agent_id} 无已连接小智端，服务器主动消息被丢弃: "
                f"{message_data.get('method')} (来源 MCP UUID: {mcp_connection_uuid})"
            )
            return
        for robot_conn in robot_connections:
            await connection_manager.forward_to_robot_by_uuid(
                robot_conn.connection_uuid, message_data
            )

    def _is_tools_list_response(self, message_data: dict) -> bool:
        """检查是否为工具列表响应"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and "result" in message_data
            and isinstance(message_data.get("result"), dict)
            and "tools" in message_data.get("result", {})
        )

    def _is_initialize_response(self, message_data: dict) -> bool:
        """检查是否为初始化响应"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and "result" in message_data
            and isinstance(message_data.get("result"), dict)
            and "protocolVersion" in message_data.get("result", {})
        )

    def _handle_tools_list_response(self, agent_id: str, mcp_connection_uuid: str, message_data: dict):
        """处理工具列表响应"""
        try:
            result = message_data.get("result", {})
            tools = result.get("tools", [])
            
            # 更新连接管理器中的工具列表
            connection_manager.update_tool_list(agent_id, mcp_connection_uuid, tools)
        except Exception:
            logger.exception("处理工具列表响应时发生错误")

    def _handle_initialize_response(self, agent_id: str, mcp_connection_uuid: str, message_data: dict):
        """处理初始化响应"""
        try:
            result = message_data.get("result", {})
            # 更新服务器信息
            connection_manager.update_server_info(agent_id, mcp_connection_uuid, result)
        except Exception:
            logger.exception("处理初始化响应时发生错误")

    async def _handle_robot_message(
        self, agent_id: str, message: str, connection_uuid: str
    ):
        """处理小智端消息"""
        logger.debug(
            f"收到小智端消息: {agent_id} (UUID: {connection_uuid}) - {message}"
        )

        try:
            message_data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"小智端消息不是有效的 JSON: {message}")
            error_response = JSONRPCProtocol.create_error_response(
                error_code=JSONRPCProtocol.PARSE_ERROR,
                error_message="消息不是有效的 JSON",
            )
            await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, JSONRPCProtocol.to_dict(error_response)
            )
            return

        if not isinstance(message_data, dict):
            logger.warning(f"小智端消息不是 JSON 对象: {message}")
            error_response = JSONRPCProtocol.create_error_response(
                error_code=JSONRPCProtocol.INVALID_REQUEST,
                error_message="请求必须是 JSON 对象",
            )
            await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, JSONRPCProtocol.to_dict(error_response)
            )
            return

        try:
            request_id = message_data.get("id")

            if self._is_tool_call_request(message_data):
                logger.info(f"收到工具调用请求: {agent_id} (UUID: {connection_uuid})")
                await self._handle_tool_call_request(
                    message_data, agent_id, connection_uuid, request_id
                )
                return

            transformed_message_data = connection_manager.transform_jsonrpc_message(
                message_data, connection_uuid
            )
            transformed_message = json.dumps(
                transformed_message_data, ensure_ascii=False
            )
            logger.debug(
                f"转换后的消息ID: {message_data.get('id')} -> {transformed_message_data.get('id')}"
            )

            mcp_connections = connection_manager.get_agent_mcp_connections(agent_id)
            if not mcp_connections:
                logger.warning(f"智能体没有连接的MCP服务器: {agent_id}")
                # JSON-RPC 通知（无 id）不应收到任何响应，仅请求才回错误。
                if request_id is not None:
                    await connection_manager.forward_to_robot_by_uuid(
                        connection_uuid,
                        create_tool_not_connected_error(request_id, agent_id),
                    )
                return

            connected_connections = [
                mcp_connection_uuid
                for mcp_connection_uuid in mcp_connections
                if connection_manager.is_mcp_server_connected(agent_id, mcp_connection_uuid)
            ]
            if not connected_connections:
                logger.error(f"没有可用的MCP服务器: {agent_id}")
                # 通知（无 id）不应收到任何响应。
                if request_id is not None:
                    await connection_manager.forward_to_robot_by_uuid(
                        connection_uuid,
                        create_forward_failed_error(request_id, agent_id),
                    )
                return

            transformed_request_id = transformed_message_data.get("id")
            if transformed_request_id is not None:
                connection_manager.register_pending_response(
                    transformed_request_id,
                    request_id,
                    connection_uuid,
                    connected_connections,
                    message_data.get("method"),
                )

            success_connections = []
            failed_connections = []
            for mcp_connection_uuid in connected_connections:
                logger.debug(
                    f"转发消息给MCP服务器: {agent_id} (UUID: {mcp_connection_uuid})"
                )
                success = await connection_manager.forward_to_mcp_server(
                    agent_id, mcp_connection_uuid, transformed_message
                )
                if success:
                    success_connections.append(mcp_connection_uuid)
                else:
                    failed_connections.append(mcp_connection_uuid)

            if not success_connections:
                logger.error(f"转发消息给所有MCP服务器失败: {agent_id}")
                if transformed_request_id is not None:
                    connection_manager.remove_pending_response(transformed_request_id)
                if request_id is not None:
                    await connection_manager.forward_to_robot_by_uuid(
                        connection_uuid,
                        create_forward_failed_error(request_id, agent_id),
                    )
                return

            if failed_connections and transformed_request_id is not None:
                for mcp_connection_uuid in failed_connections:
                    pending = connection_manager.discard_expected_connection(
                        transformed_request_id, mcp_connection_uuid
                    )
                    if pending is not None:
                        await self._forward_aggregated_response(
                            pending, transformed_request_id
                        )

        except Exception as e:
            logger.exception(f"处理小智端消息时发生错误: {e}")
            # 通知（无 id）不应收到响应，仅对真正的请求回内部错误。
            if "request_id" in locals() and request_id is not None:
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.INTERNAL_ERROR,
                    "处理请求时发生内部错误",
                )

    async def _forward_aggregated_response(self, pending, transformed_request_id: str) -> None:
        """聚合待处理响应并回送小智端，随后清理 pending。"""
        aggregated_response = connection_manager.aggregate_responses(pending)
        connection_uuid, restored_message = connection_manager.restore_jsonrpc_message(
            aggregated_response
        )
        if connection_uuid:
            success = await connection_manager.forward_to_robot_by_uuid(
                connection_uuid, restored_message
            )
            if not success:
                logger.error(f"转发聚合响应给小智端失败: {connection_uuid}")
        else:
            logger.error("聚合响应缺少目标小智端连接信息")
        connection_manager.remove_pending_response(transformed_request_id)

    def _is_tool_call_request(self, message_data: dict) -> bool:
        """检查是否为工具调用请求"""
        return (
            message_data.get("jsonrpc") == "2.0"
            and message_data.get("method") == "tools/call"
        )

    async def _handle_tool_call_request(self, message_data: dict, agent_id: str, connection_uuid: str, request_id):
        """处理工具调用请求"""
        try:
            params = message_data.get("params", {})
            tool_name = params.get("name")
            
            if not tool_name:
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.INVALID_PARAMS,
                    "缺少工具名称",
                )
                return
            
            # 查找工具对应的服务器
            mcp_connection_uuid = connection_manager.find_tool_connection(agent_id, tool_name)

            if not mcp_connection_uuid:
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.METHOD_NOT_FOUND,
                    f"工具 '{tool_name}' 不存在",
                )
                return
            
            # 检查服务器是否连接
            if not connection_manager.is_mcp_server_connected(agent_id, mcp_connection_uuid):
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.TOOL_NOT_CONNECTED,
                    "工具对应的MCP连接未连接",
                )
                return
            
            # 转换消息ID并转发给对应的MCP服务器
            transformed_message_data = connection_manager.transform_jsonrpc_message(
                message_data, connection_uuid
            )
            transformed_message = json.dumps(
                transformed_message_data, ensure_ascii=False
            )
            
            # 注册待处理的响应（单个服务器）
            transformed_request_id = transformed_message_data.get("id")
            if transformed_request_id is not None:
                connection_manager.register_pending_response(
                    transformed_request_id,
                    request_id,
                    connection_uuid,
                    [mcp_connection_uuid],
                    message_data.get("method"),
                )
            
            # 转发给MCP服务器
            success = await connection_manager.forward_to_mcp_server(
                agent_id, mcp_connection_uuid, transformed_message
            )
            
            if not success:
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.FORWARD_FAILED,
                    "转发工具调用请求失败",
                )
                if transformed_request_id is not None:
                    connection_manager.remove_pending_response(transformed_request_id)
                return

            logger.info(f"已转发工具调用请求: {tool_name} -> {agent_id} (UUID: {mcp_connection_uuid})")
            
        except Exception as e:
            logger.exception(f"处理工具调用请求时发生错误: {e}")
            try:
                await self._reply_error(
                    connection_uuid,
                    request_id,
                    JSONRPCProtocol.INTERNAL_ERROR,
                    "处理工具调用请求时发生错误",
                )
            except Exception:
                logger.exception("回送工具调用错误响应失败")


# 全局WebSocket处理器实例
websocket_handler = WebSocketHandler()
