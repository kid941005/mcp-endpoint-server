"""
连接管理器
负责管理WebSocket连接和消息转发
"""

import asyncio
import json
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
from ..utils.logger import get_logger

logger = get_logger()


class RobotConnection:
    """小智端连接信息"""

    def __init__(self, websocket: Any, agent_id: str):
        self.websocket = websocket
        self.agent_id = agent_id
        self.connection_uuid = str(uuid.uuid4())
        self.timestamp = time.time()


class MCPServerConnection:
    """MCP服务器连接信息"""

    def __init__(self, websocket: Any, agent_id: str):
        self.websocket = websocket
        self.agent_id = agent_id
        self.connection_uuid = str(uuid.uuid4())
        self.timestamp = time.time()
        self.tools: Dict[str, Any] = {}  # 存储工具列表
        self.server_info: Dict[str, Any] = {}  # 存储服务器信息


class PendingResponse:
    """待处理的响应信息"""

    def __init__(self, transformed_request_id: str, request_id: Any, connection_uuid: str, expected_connections: List[str], method: Optional[str] = None):
        self.transformed_request_id = transformed_request_id
        self.request_id = request_id
        self.connection_uuid = connection_uuid
        self.expected_connections = set(expected_connections)
        self.received_responses = {}
        self.method = method
        self.timestamp = time.time()
        self.timeout_task: Optional[asyncio.Task] = None


class ConnectionManager:
    """连接管理器"""

    def __init__(self):
        # MCP服务器连接: {agent_id: {connection_uuid: MCPServerConnection}}
        self.mcp_server_connections: Dict[str, Dict[str, MCPServerConnection]] = {}
        # 小智端连接: {connection_uuid: RobotConnection}
        self.robot_connections: Dict[str, RobotConnection] = {}
        # 待处理的响应: {transformed_request_id: PendingResponse}
        self.pending_responses: Dict[str, PendingResponse] = {}
        # 连接锁
        self._lock = asyncio.Lock()
        # 待处理响应超时（秒），避免任一 MCP 不响应时永久挂起并泄漏
        self.pending_timeout = 30.0

    async def register_mcp_server_connection(
        self, agent_id: str, websocket: Any
    ) -> str:
        """注册MCP服务器连接，返回分配的UUID"""
        async with self._lock:
            # 初始化agent_id的连接字典
            if agent_id not in self.mcp_server_connections:
                self.mcp_server_connections[agent_id] = {}

            # 创建新的MCP服务器连接
            mcp_conn = MCPServerConnection(websocket, agent_id)
            self.mcp_server_connections[agent_id][mcp_conn.connection_uuid] = mcp_conn
            logger.info(f"MCP服务器连接已注册: {agent_id} (UUID: {mcp_conn.connection_uuid})")
            return mcp_conn.connection_uuid

    async def register_robot_connection(
        self, agent_id: str, websocket: Any
    ) -> str:
        """注册小智端连接，返回分配的UUID"""
        async with self._lock:
            robot_conn = RobotConnection(websocket, agent_id)
            self.robot_connections[robot_conn.connection_uuid] = robot_conn
            logger.info(
                f"小智端连接已注册: {agent_id}, UUID: {robot_conn.connection_uuid}"
            )
            return robot_conn.connection_uuid

    async def unregister_mcp_server_connection(self, agent_id: str, mcp_connection_uuid: str):
        """注销MCP服务器连接"""
        async with self._lock:
            if agent_id in self.mcp_server_connections:
                if mcp_connection_uuid in self.mcp_server_connections[agent_id]:
                    del self.mcp_server_connections[agent_id][mcp_connection_uuid]
                    # 如果该agent_id下没有其他服务器连接，清理agent_id
                    if not self.mcp_server_connections[agent_id]:
                        del self.mcp_server_connections[agent_id]
                    logger.info(f"MCP服务器连接已注销: {agent_id} (UUID: {mcp_connection_uuid})")

    async def unregister_robot_connection(self, connection_uuid: str):
        """注销小智端连接"""
        async with self._lock:
            if connection_uuid in self.robot_connections:
                robot_conn = self.robot_connections[connection_uuid]
                del self.robot_connections[connection_uuid]
                logger.info(
                    f"小智端连接已注销: {robot_conn.agent_id}, UUID: {connection_uuid}"
                )

    def _transform_jsonrpc_id(self, original_id: Any, connection_uuid: str) -> str:
        """转换JSON-RPC ID"""
        if isinstance(original_id, int):
            return f"{connection_uuid}_n_{original_id}"
        elif isinstance(original_id, str):
            return f"{connection_uuid}_s_{original_id}"
        else:
            # 其他类型转换为字符串
            return f"{connection_uuid}_s_{str(original_id)}"

    def _restore_jsonrpc_id(self, transformed_id: Any) -> Tuple[Optional[str], Any]:
        """还原JSON-RPC ID，返回(connection_uuid, original_id)"""
        if not isinstance(transformed_id, str) or "_" not in transformed_id:
            return None, None

        # 解析格式: uuid_type_original_id
        parts = transformed_id.split("_", 2)
        if len(parts) < 3:
            return None, None

        connection_uuid = parts[0]
        id_type = parts[1]
        original_id_part = parts[2]

        if id_type == "n":
            # 数字类型
            try:
                original_id = int(original_id_part)
            except ValueError:
                original_id = original_id_part
        elif id_type == "s":
            # 字符串类型
            if original_id_part == "null":
                original_id = None
            else:
                original_id = original_id_part
        else:
            # 未知类型，保持原样
            original_id = original_id_part

        return connection_uuid, original_id

    def transform_jsonrpc_message(
        self, message: Dict[str, Any], connection_uuid: str
    ) -> Dict[str, Any]:
        """转换JSON-RPC消息的ID"""
        if not isinstance(message, dict):
            return message

        # 创建消息副本
        transformed_message = message.copy()

        # 转换ID（id=0 是合法的 JSON-RPC 请求 ID，不能用真值判断）
        if "id" in transformed_message:
            original_id = transformed_message["id"]
            if original_id is not None:
                transformed_message["id"] = self._transform_jsonrpc_id(
                    original_id, connection_uuid
                )

        return transformed_message

    def restore_jsonrpc_message(
        self, message: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """还原JSON-RPC消息的ID，返回(connection_uuid, restored_message)"""
        if not isinstance(message, dict):
            return None, message

        # 创建消息副本
        restored_message = message.copy()

        # 还原ID
        if "id" in restored_message:
            transformed_id = restored_message["id"]
            connection_uuid, original_id = self._restore_jsonrpc_id(transformed_id)
            if connection_uuid:
                restored_message["id"] = original_id
                return connection_uuid, restored_message

        return None, restored_message

    async def forward_to_mcp_server(self, agent_id: str, mcp_connection_uuid: str, message: Any) -> bool:
        """转发消息给指定的MCP服务器"""
        # 只在锁内查找连接，绝不在持锁期间执行网络 I/O。
        async with self._lock:
            agent_connections = self.mcp_server_connections.get(agent_id)
            if not agent_connections:
                logger.warning(f"智能体连接不存在: {agent_id}")
                return False
            mcp_conn = agent_connections.get(mcp_connection_uuid)
            if mcp_conn is None:
                logger.warning(f"MCP服务器连接不存在: {agent_id} (UUID: {mcp_connection_uuid})")
                return False

        if isinstance(message, dict):
            message_str = json.dumps(message, ensure_ascii=False)
        elif isinstance(message, str):
            message_str = message
        else:
            message_str = str(message)

        try:
            await asyncio.wait_for(mcp_conn.websocket.send_text(message_str), timeout=5.0)
            logger.debug(f"消息已转发给MCP服务器 {agent_id} (UUID: {mcp_connection_uuid}): {message_str}")
            return True
        except Exception as e:
            # Starlette 的 WebSocket 在断开时通常抛 RuntimeError/WebSocketDisconnect/OSError，
            # 而不仅仅是 websockets.ConnectionClosed；任何发送失败都视为连接失效并清理。
            logger.warning(f"MCP服务器连接已断开，转发失败: {agent_id} (UUID: {mcp_connection_uuid}) - {e}")
            # 此处已释放 _lock，unregister 可以安全地重新获取锁。
            await self.unregister_mcp_server_connection(agent_id, mcp_connection_uuid)
            return False

    async def forward_to_robot_by_uuid(
        self, connection_uuid: str, message: Any
    ) -> bool:
        """根据UUID转发消息给特定的小智端连接"""
        # 只在锁内查找连接，绝不在持锁期间执行网络 I/O。
        async with self._lock:
            robot_conn = self.robot_connections.get(connection_uuid)
            if robot_conn is None:
                logger.warning(f"小智端连接不存在: {connection_uuid}")
                return False

        if isinstance(message, dict):
            message_str = json.dumps(message, ensure_ascii=False)
        elif isinstance(message, str):
            message_str = message
        else:
            message_str = str(message)

        try:
            await asyncio.wait_for(robot_conn.websocket.send_text(message_str), timeout=5.0)
            logger.debug(
                f"消息已转发给小智端 {robot_conn.agent_id} (UUID: {connection_uuid}): {message_str}"
            )
            return True
        except Exception as e:
            logger.warning(f"小智端连接已断开，转发失败: {connection_uuid} - {e}")
            # 此处已释放 _lock，unregister 可以安全地重新获取锁。
            await self.unregister_robot_connection(connection_uuid)
            return False

    def register_pending_response(
        self,
        transformed_request_id: str,
        request_id: Any,
        connection_uuid: str,
        expected_connections: List[str],
        method: Optional[str] = None,
    ):
        """注册待处理的响应。

        同一 transformed_request_id 重复注册时先取消旧超时任务，避免旧任务
        到期后取走新的 pending 并向机器人误报超时。
        """
        previous = self.pending_responses.get(transformed_request_id)
        if previous is not None and previous.timeout_task and not previous.timeout_task.done():
            previous.timeout_task.cancel()
        pending = PendingResponse(
            transformed_request_id, request_id, connection_uuid, expected_connections, method
        )
        self.pending_responses[transformed_request_id] = pending
        pending.timeout_task = asyncio.create_task(
            self._expire_pending_response(transformed_request_id)
        )
        logger.debug(f"注册待处理响应: {transformed_request_id}, 期望连接: {expected_connections}")

    async def _expire_pending_response(self, transformed_request_id: str):
        """超时清理未完成的请求，避免永久挂起与内存泄漏。"""
        try:
            await asyncio.sleep(self.pending_timeout)
        except asyncio.CancelledError:
            return
        pending = self.pending_responses.pop(transformed_request_id, None)
        if pending is None:
            return
        logger.warning(f"等待 MCP 响应超时: {transformed_request_id}")
        error_message = {
            "jsonrpc": "2.0",
            "id": pending.request_id,
            "error": {
                "code": -32002,
                "message": "等待工具端响应超时",
                "data": {"timeout_seconds": self.pending_timeout},
            },
        }
        await self.forward_to_robot_by_uuid(pending.connection_uuid, error_message)

    def add_server_response(self, transformed_request_id: str, mcp_connection_uuid: str, response: Dict[str, Any]):
        """添加服务器响应"""
        if transformed_request_id in self.pending_responses:
            pending = self.pending_responses[transformed_request_id]
            pending.received_responses[mcp_connection_uuid] = response
            logger.debug(f"收到服务器响应: {transformed_request_id}/{mcp_connection_uuid}")

            # 检查是否所有期望的服务器都已响应
            if pending.expected_connections.issubset(set(pending.received_responses.keys())):
                return pending
        return None

    def discard_expected_connection(self, transformed_request_id: str, mcp_connection_uuid: str):
        """转发失败时缩减期望集合；若剩余连接已全部响应则返回 pending。"""
        pending = self.pending_responses.get(transformed_request_id)
        if pending is None:
            return None
        pending.expected_connections.discard(mcp_connection_uuid)
        if not pending.expected_connections:
            return pending
        if pending.expected_connections.issubset(set(pending.received_responses.keys())):
            return pending
        return None

    def remove_pending_response(self, transformed_request_id: str):
        """移除待处理的响应"""
        pending = self.pending_responses.pop(transformed_request_id, None)
        if pending is not None:
            if pending.timeout_task and not pending.timeout_task.done():
                pending.timeout_task.cancel()
            logger.debug(f"移除待处理响应: {transformed_request_id}")

    def aggregate_responses(self, pending: PendingResponse) -> Dict[str, Any]:
        """聚合多个服务器的响应。

        JSON-RPC 响应必须 result/error 二选一；任一子响应错误时直接返回该错误，
        不能把 error 对象混入 tools/content 数组产出非法 MCP 结果。
        """
        try:
            responses = list(pending.received_responses.values())
            response_id = pending.transformed_request_id
            if responses:
                response_id = responses[0].get("id", pending.transformed_request_id)

            errors = [response["error"] for response in responses if "error" in response]
            if errors:
                return {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "error": errors[0],
                }

            successful = [response["result"] for response in responses if "result" in response]
            if not successful:
                return {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "error": {
                        "code": -32603,
                        "message": "工具端未返回有效结果",
                    },
                }

            tools: List[Any] = []
            content: List[Any] = []
            is_tools = False
            is_content = False
            for result in successful:
                if isinstance(result, dict) and "tools" in result:
                    is_tools = True
                    tools.extend(result.get("tools") or [])
                elif isinstance(result, dict) and "content" in result:
                    is_content = True
                    content.extend(result.get("content") or [])

            if is_tools:
                aggregated = {"tools": tools}
            elif is_content:
                aggregated = {"content": content}
            elif len(successful) == 1:
                aggregated = successful[0]
            else:
                # initialize/ping 等无合并语义的方法必须返回单个合法 result，
                # 不能包装成 {"responses": [...]} 破坏 MCP 协议结构。
                logger.warning(
                    f"多个工具端返回无合并语义的响应，取首个: "
                    f"{pending.transformed_request_id} (method={pending.method}, "
                    f"count={len(successful)})"
                )
                aggregated = successful[0]

            logger.debug(f"聚合响应完成: {len(successful)} 个成功响应")
            return {"jsonrpc": "2.0", "id": response_id, "result": aggregated}

        except Exception as e:
            logger.error(f"聚合响应时发生错误: {e}")
            return {
                "jsonrpc": "2.0",
                "id": pending.request_id,
                "error": {
                    "code": -32603,
                    "message": "聚合响应时发生错误",
                    "data": {"details": str(e)},
                },
            }

    def update_tool_list(self, agent_id: str, mcp_connection_uuid: str, tools: List[Dict[str, Any]]):
        """更新工具列表；跨服务器同名工具会告警（调用路由存在歧义）。"""
        if agent_id in self.mcp_server_connections and mcp_connection_uuid in self.mcp_server_connections[agent_id]:
            mcp_conn = self.mcp_server_connections[agent_id][mcp_connection_uuid]
            mcp_conn.tools = {tool["name"]: tool for tool in tools}
            logger.debug(f"已更新工具列表: {agent_id} (UUID: {mcp_connection_uuid}), 工具数量: {len(tools)}")
            for other_uuid, other_conn in self.mcp_server_connections[agent_id].items():
                if other_uuid == mcp_connection_uuid:
                    continue
                duplicated = set(mcp_conn.tools) & set(other_conn.tools)
                if duplicated:
                    logger.warning(
                        f"检测到跨服务器同名工具: {agent_id} "
                        f"{mcp_connection_uuid} 与 {other_uuid}: {sorted(duplicated)}；"
                        "tools/call 与 tools/list 均取 UUID 最小的连接"
                    )

    def update_server_info(self, agent_id: str, mcp_connection_uuid: str, server_info: Dict[str, Any]):
        """更新服务器信息"""
        if agent_id in self.mcp_server_connections and mcp_connection_uuid in self.mcp_server_connections[agent_id]:
            mcp_conn = self.mcp_server_connections[agent_id][mcp_connection_uuid]
            mcp_conn.server_info = server_info
            logger.debug(f"已更新服务器信息: {agent_id} (UUID: {mcp_connection_uuid})")

    def _select_tool_connections(self, agent_id: str, tool_name: str) -> List[str]:
        """返回暴露该工具名的连接 UUID（按 UUID 排序）。

        tools/list 的去重与 tools/call 的路由共用这一顺序，
        保证客户端看到的工具与调用实际打到的服务器是同一台。
        """
        servers = self.mcp_server_connections.get(agent_id) or {}
        return sorted(
            mcp_connection_uuid
            for mcp_connection_uuid, mcp_conn in servers.items()
            if tool_name in mcp_conn.tools
        )

    @staticmethod
    def _with_shadow_meta(
        tool_info: Dict[str, Any], connection_uuids: List[str]
    ) -> Dict[str, Any]:
        """同名工具被遮蔽时附加 _meta 说明；不改 name/description/inputSchema。

        客户端可忽略未知 _meta，因此对现有客户端零影响，仅供运维追溯。
        """
        if len(connection_uuids) <= 1:
            return tool_info
        shadowed = dict(tool_info)
        meta = dict(shadowed.get("_meta") or {})
        meta["mcp-endpoint-server"] = {"shadowed_by": connection_uuids[1:]}
        shadowed["_meta"] = meta
        return shadowed

    def get_all_tools(self, agent_id: str) -> List[Dict[str, Any]]:
        """获取指定智能体的所有可用工具列表（同名工具只保留 UUID 最小者）"""
        servers = self.mcp_server_connections.get(agent_id) or {}
        tools_by_name: Dict[str, Dict[str, Any]] = {}
        for mcp_connection_uuid in sorted(servers):
            for tool_name, tool_info in servers[mcp_connection_uuid].tools.items():
                if tool_name in tools_by_name:
                    continue
                tools_by_name[tool_name] = self._with_shadow_meta(
                    tool_info, self._select_tool_connections(agent_id, tool_name)
                )
        return [tools_by_name[tool_name] for tool_name in sorted(tools_by_name)]

    def get_agent_tools_summary(self, agent_id: str) -> Dict[str, Any]:
        """获取指定智能体的工具汇总信息（同名工具保留 UUID 最小的连接）"""
        servers = self.mcp_server_connections.get(agent_id) or {}
        tool_names = sorted(
            {tool_name for mcp_conn in servers.values() for tool_name in mcp_conn.tools}
        )
        tools = []
        for tool_name in tool_names:
            connection_uuids = self._select_tool_connections(agent_id, tool_name)
            tool_info = servers[connection_uuids[0]].tools[tool_name]
            tools.append(self._with_shadow_meta(tool_info, connection_uuids))
        return {
            "agent_id": agent_id,
            "tools": tools,
        }

    def find_tool_connection(self, agent_id: str, tool_name: str) -> Optional[str]:
        """根据工具名称查找对应的连接UUID。

        同名工具可能存在于多个服务器；按 UUID 排序取首个以保证确定性，
        与 tools/list 的去重顺序一致，并对歧义告警，便于排查错误路由。
        """
        matches = self._select_tool_connections(agent_id, tool_name)
        if len(matches) > 1:
            logger.warning(
                f"工具名存在歧义，路由到 {matches[0]}: {agent_id}/{tool_name} -> {matches}"
            )
        return matches[0] if matches else None

    def get_connection_stats(self) -> Dict[str, Any]:
        """获取连接统计信息"""
        # 统计每个agent_id的连接数
        agent_connection_counts = {}
        for robot_conn in self.robot_connections.values():
            agent_id = robot_conn.agent_id
            agent_connection_counts[agent_id] = (
                agent_connection_counts.get(agent_id, 0) + 1
            )

        # 统计MCP服务器信息
        mcp_server_stats = {}
        total_mcp_connections = 0
        total_tools = 0
        
        for agent_id, servers in self.mcp_server_connections.items():
            mcp_server_stats[agent_id] = {}
            for mcp_connection_uuid, mcp_conn in servers.items():
                total_mcp_connections += 1
                tool_count = len(mcp_conn.tools)
                total_tools += tool_count
                mcp_server_stats[agent_id][mcp_connection_uuid] = {
                    "tools_count": tool_count,
                    "tools": list(mcp_conn.tools.keys()),
                    "server_info": mcp_conn.server_info
                }

        # 同名工具冲突：同一 agent 下被多个服务器暴露的工具名
        tool_conflicts = []
        for agent_id, servers in self.mcp_server_connections.items():
            tool_names = sorted(
                {tool_name for mcp_conn in servers.values() for tool_name in mcp_conn.tools}
            )
            for tool_name in tool_names:
                connection_uuids = self._select_tool_connections(agent_id, tool_name)
                if len(connection_uuids) > 1:
                    tool_conflicts.append(
                        {
                            "agent_id": agent_id,
                            "tool": tool_name,
                            "uuids": connection_uuids,
                        }
                    )

        return {
            "mcp_server_connections": total_mcp_connections,
            "robot_connections": len(self.robot_connections),
            "total_connections": total_mcp_connections + len(self.robot_connections),
            "robot_connections_by_agent": agent_connection_counts,
            "mcp_servers": mcp_server_stats,
            "total_tools": total_tools,
            "tool_conflicts": tool_conflicts,
        }

    def is_mcp_server_connected(self, agent_id: str, mcp_connection_uuid: str) -> bool:
        """检查MCP服务器是否已连接"""
        return (agent_id in self.mcp_server_connections and 
                mcp_connection_uuid in self.mcp_server_connections[agent_id])

    def is_robot_connected(self, agent_id: str) -> bool:
        """检查小智端是否已连接"""
        return any(
            conn.agent_id == agent_id for conn in self.robot_connections.values()
        )

    def get_robot_connections_by_agent(self, agent_id: str) -> List[RobotConnection]:
        """获取指定agent_id的所有小智端连接"""
        return [
            conn
            for conn in self.robot_connections.values()
            if conn.agent_id == agent_id
        ]

    def get_available_agents(self) -> List[str]:
        """获取所有可用的智能体ID列表"""
        return list(self.mcp_server_connections.keys())

    def get_agent_mcp_connections(self, agent_id: str) -> List[str]:
        """获取指定智能体的所有MCP连接UUID列表"""
        if agent_id in self.mcp_server_connections:
            return list(self.mcp_server_connections[agent_id].keys())
        return []


    async def shutdown(self) -> None:
        """优雅停机：取消待处理超时任务并关闭所有 WebSocket。"""
        for pending in list(self.pending_responses.values()):
            if pending.timeout_task and not pending.timeout_task.done():
                pending.timeout_task.cancel()
        self.pending_responses.clear()

        mcp_websockets = [
            conn.websocket
            for servers in self.mcp_server_connections.values()
            for conn in servers.values()
        ]
        robot_websockets = [conn.websocket for conn in self.robot_connections.values()]
        self.mcp_server_connections.clear()
        self.robot_connections.clear()

        for websocket in mcp_websockets + robot_websockets:
            try:
                await websocket.close(code=1001)
            except Exception as e:
                logger.debug(f"关闭连接时忽略异常: {e}")


# 全局连接管理器实例
connection_manager = ConnectionManager()
