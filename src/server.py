"""
MCP Endpoint Server
主服务器文件
"""

import hmac
import json
from typing import Optional

import uvicorn
from .utils.config import config
from .utils.logger import get_logger
from .utils.aes_utils import decrypt
from .utils.jsonrpc import (
    JSONRPCProtocol,
)
from .utils.util import get_local_ip
from .utils import __version__
from contextlib import asynccontextmanager
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from .core.connection_manager import connection_manager
from .handlers.websocket_handler import websocket_handler
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect

logger = get_logger()


async def validate_token_and_get_agent_id(websocket: WebSocket) -> Optional[str]:
    """
    验证token并获取agentId的公共方法

    Args:
        websocket: WebSocket连接对象

    Returns:
        str: 验证成功返回agentId，失败返回None
    """
    token = websocket.query_params.get("token")
    if not token:
        logger.error("缺少token参数")
        await websocket.close(code=1008, reason="缺少token参数")
        return None

    data = decrypt(config.get("server", "key", ""), token)
    if not data:
        logger.error("token解密失败（token 已隐藏）")
        await websocket.close(code=1008, reason="token解密失败")
        return None

    try:
        data = json.loads(data)
        agent_id = data.get("agentId")
        if not agent_id:
            logger.error("无对应agentId")
            await websocket.close(code=1008, reason="无对应agentId")
            return None
        return agent_id
    except json.JSONDecodeError:
        logger.error("token数据格式错误")
        await websocket.close(code=1008, reason="token数据格式错误")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("MCP Endpoint Server 正在启动...")
    local_ip = get_local_ip()
    port = config.getint("server", "port", 8004)
    logger.info(f"健康检查地址: http://{local_ip}:{port}/mcp_endpoint/health")
    logger.info(f"工具端 MCP 接入点: ws://{local_ip}:{port}/mcp_endpoint/mcp/")
    logger.info("server.key 与加密 token 已隐藏，请从配置文件读取，请勿泄露。")
    yield
    # 关闭时
    logger.info("MCP Endpoint Server 正在关闭，释放连接与待处理请求...")
    await connection_manager.shutdown()
    logger.info("MCP Endpoint Server 已关闭")


# 创建FastAPI应用
app = FastAPI(
    title="MCP Endpoint Server",
    description="高效的WebSocket中转服务器",
    version=__version__,
    lifespan=lifespan,
)

# 配置CORS
if config.getboolean("security", "enable_cors", True):
    raw_origins = config.get("security", "allowed_origins", "*") or "*"
    allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    allow_credentials = config.getboolean("security", "allow_credentials", False)
    if "*" in allowed_origins and allow_credentials:
        # 浏览器规范禁止 Access-Control-Allow-Origin: * 与凭据同时使用。
        logger.warning("allowed_origins 为 * 时已禁用 allow_credentials")
        allow_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/")
async def redirect_root():
    """根路径重定向到 /mcp_endpoint/"""
    return RedirectResponse(url="/mcp_endpoint/")


@app.get("/mcp_endpoint/")
async def root():
    """根路径"""
    response = JSONRPCProtocol.create_success_response(
        result={
            "message": "MCP Endpoint Server",
            "version": __version__,
            "status": "running",
        }
    )
    return JSONRPCProtocol.to_dict(response)


def _extract_authorization_key(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization 头提取密钥，兼容 "Bearer <key>" 与裸密钥两种写法。"""
    if not authorization:
        return None
    value = authorization.strip()
    if not value:
        return None
    scheme, _, credentials = value.partition(" ")
    if credentials and scheme.lower() == "bearer":
        return credentials.strip() or None
    return value


_query_key_warning_emitted = False


def _is_authorized_request(authorization: Optional[str], key: Optional[str]) -> bool:
    """
    常量时间校验 server.key，避免时序侧信道。

    优先使用 Authorization 头；query 参数 key 仅为兼容旧调用方保留，
    首次命中时打印一条废弃警告，兼容窗口结束后移除。
    """
    global _query_key_warning_emitted
    expected_key = config.get("server", "key", "")

    header_key = _extract_authorization_key(authorization)
    if header_key:
        return bool(expected_key) and hmac.compare_digest(
            str(header_key), str(expected_key)
        )

    if key:
        if not _query_key_warning_emitted:
            _query_key_warning_emitted = True
            logger.warning(
                "管理接口使用 query 参数 key 已废弃，请改用 Authorization 头；"
                "query 兼容将在下一个发布周期移除"
            )
        return bool(expected_key) and hmac.compare_digest(str(key), str(expected_key))

    return False


@app.get("/mcp_endpoint/health")
async def health_check(
    authorization: Optional[str] = Header(default=None),
    key: Optional[str] = None,
    agentid: Optional[str] = None,
):
    """健康检查（Authorization: Bearer <server_key>；query key 兼容保留）"""
    if not _is_authorized_request(authorization, key):
        response = JSONRPCProtocol.create_error_response(
            error_code=JSONRPCProtocol.AUTHENTICATION_ERROR,
            error_message="密钥验证失败",
            error_data={"details": "提供的密钥无效或缺失"},
        )
        return JSONRPCProtocol.to_dict(response)

    if agentid:
        response = JSONRPCProtocol.create_success_response(
            result=connection_manager.get_agent_tools_summary(agentid)
        )
        return JSONRPCProtocol.to_dict(response)

    response = JSONRPCProtocol.create_success_response(
        result={"status": "success", "message": "ok"}
    )
    return JSONRPCProtocol.to_dict(response)


@app.get("/mcp_endpoint/stats")
async def stats(
    authorization: Optional[str] = Header(default=None),
    key: Optional[str] = None,
):
    """连接与工具统计（Authorization: Bearer <server_key>；query key 兼容保留）"""
    if not _is_authorized_request(authorization, key):
        response = JSONRPCProtocol.create_error_response(
            error_code=JSONRPCProtocol.AUTHENTICATION_ERROR,
            error_message="密钥验证失败",
            error_data={"details": "提供的密钥无效或缺失"},
        )
        return JSONRPCProtocol.to_dict(response)

    return JSONRPCProtocol.to_dict(
        JSONRPCProtocol.create_success_response(result=connection_manager.get_connection_stats())
    )


@app.websocket("/mcp_endpoint/mcp/")
async def websocket_tool_endpoint(websocket: WebSocket):
    """MCP服务器端WebSocket端点"""
    # 先鉴权再握手：未通过时 validate 内部直接 close，不建立可用连接。
    agent_id = await validate_token_and_get_agent_id(websocket)
    if not agent_id:
        return
    await websocket.accept()

    mcp_connection_uuid = None
    try:
        # 注册连接
        mcp_connection_uuid = await connection_manager.register_mcp_server_connection(agent_id, websocket)
        logger.info(f"MCP服务器连接已建立: {agent_id} (UUID: {mcp_connection_uuid})")

        # 处理消息
        while True:
            try:
                message = await websocket.receive_text()
                await websocket_handler._handle_mcp_server_message(agent_id, mcp_connection_uuid, message)
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"处理MCP服务器消息时发生错误: {e}")
                break

    except Exception as e:
        logger.error(f"处理MCP服务器连接时发生错误: {e}")
    finally:
        if mcp_connection_uuid:
            await connection_manager.unregister_mcp_server_connection(agent_id, mcp_connection_uuid)
            logger.info(f"MCP服务器连接已关闭: {agent_id} (UUID: {mcp_connection_uuid})")


@app.websocket("/mcp_endpoint/call/")
async def websocket_robot_endpoint(websocket: WebSocket):
    """小智端WebSocket端点"""
    # 先鉴权再握手：未通过时 validate 内部直接 close，不建立可用连接。
    agent_id = await validate_token_and_get_agent_id(websocket)
    if not agent_id:
        return
    await websocket.accept()

    connection_uuid = None
    try:
        # 注册连接并获取UUID
        connection_uuid = await connection_manager.register_robot_connection(
            agent_id, websocket
        )
        logger.info(f"小智端连接已建立: {agent_id} (UUID: {connection_uuid})")

        # 处理消息
        while True:
            try:
                message = await websocket.receive_text()
                await websocket_handler._handle_robot_message(
                    agent_id, message, connection_uuid
                )
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"处理小智端消息时发生错误: {e}")
                break

    except Exception as e:
        logger.error(f"处理小智端连接时发生错误: {e}")
    finally:
        if connection_uuid:
            await connection_manager.unregister_robot_connection(connection_uuid)
            logger.info(f"小智端连接已关闭: {agent_id} (UUID: {connection_uuid})")


def main():
    """主函数"""
    # 设置uvicorn日志拦截
    from .utils.logger import logger_manager

    logger_manager.setup_uvicorn_logging()

    # 获取配置
    host = config.get("server", "host", "127.0.0.1")
    port = config.getint("server", "port", 8004)
    debug = config.getboolean("server", "debug", False)

    logger.info(f"启动MCP Endpoint Server: {host}:{port}")

    # 启动服务器
    uvicorn.run(
        "src.server:app",
        host=host,
        port=port,
        reload=debug,
        log_level=config.get("server", "log_level", "INFO").lower(),
        access_log=False,
        log_config=None,  # 禁用uvicorn默认日志配置
        use_colors=True,  # 启用颜色支持
    )


if __name__ == "__main__":
    main()
