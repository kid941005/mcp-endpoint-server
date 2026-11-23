# MCP Endpoint Server

一个高效的xiaozhi mcp接入点服务器，用于[xiaozhi-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)自定义mcp服务注册，方便拓展小智设备工具调用。

本项目是一个基于websocket的MCP注册中心，功能单一，建议使用Docker运行。

## 功能特性

- 🚀 **高性能**: 基于FastAPI和WebSocket的异步架构，支持高并发
- 🔄 **消息转发**: 自动转发工具端和小智端之间的消息
- 🔒 **连接管理**: 智能管理WebSocket连接，支持连接清理
- 📊 **监控统计**: 提供受密钥保护的连接统计和健康检查接口
- ⚙️ **配置灵活**: 支持配置文件管理，易于部署和维护
- 🛡️ **错误处理**: 完善的错误处理和日志记录
- 🌐 **多服务器支持**: 支持一个智能体连接多个MCP服务器，统一管理工具列表和调用
- 🎯 **智能路由**: 根据工具名称自动路由到正确的服务器
- 📋 **统一工具列表**: 小智端可以获取所有服务器的工具列表

## 架构设计

![原理图](./docs/schematic.png)

## 接入教程

[MCP 接入点部署使用指南](https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/main/docs/mcp-endpoint-integration.md)

## 工具端桥接

如果 MCP Server 使用 **stdio / SSE / Streamable HTTP** 而不是原生 WebSocket，
可以使用内置的 [mcp-bridge](./mcp-bridge/) 把它转换为本项目的工具端 WebSocket 协议。

- 目录：[mcp-bridge/](./mcp-bridge/)
- 详细说明：[mcp-bridge/README.md](./mcp-bridge/README.md)
- 支持传输：`stdio`、`sse`、`streamable-http`
- 特性：原始 JSON-RPC 透传、断线重连、Docker 部署、日志隐藏 token
- 依赖：mcp 2.3.x（`mcp>=2.3.0,<3`）与 `httpx2`；bridge 不解析 `initialize`，兼容 MCP 协议 `2025-06-18`

### 快速使用

1. 从 endpoint server 日志获取工具端地址，填入 `MCP_ENDPOINT`：
   `ws://<ip>:8004/mcp_endpoint/mcp/?token=...`。token 已经过 URL 编码，**不要再次编码**。
2. 按 MCP Server 的传输方式启动 bridge：

```bash
# stdio
MCP_ENDPOINT='ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=...' \
MCP_TRANSPORT=stdio \
MCP_COMMAND=python \
MCP_ARGS='["/app/server.py"]' \
python mcp_bridge.py

# SSE
MCP_ENDPOINT='ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=...' \
MCP_TRANSPORT=sse \
MCP_URL='http://127.0.0.1:8080/sse' \
python mcp_bridge.py

# Streamable HTTP
MCP_ENDPOINT='ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=...' \
MCP_TRANSPORT=streamable-http \
MCP_URL='http://127.0.0.1:8080/mcp' \
python mcp_bridge.py
```

Docker 方式见 [mcp-bridge/docker-compose.example.yml](./mcp-bridge/docker-compose.example.yml)，
完整环境变量与排障说明见 [mcp-bridge/README.md](./mcp-bridge/README.md)。

## 接口与安全

- `GET /mcp_endpoint/`：服务信息（版本、运行状态）。
- `GET /mcp_endpoint/health`：健康检查，请求头需带 `Authorization: Bearer <server_key>`。
- `GET /mcp_endpoint/stats`：连接数与待处理请求统计，鉴权方式同上。
- 旧写法 `?key=<server_key>` 兼容保留一个发布周期（命中时日志会提示废弃），下个周期移除；请求头优先于 query。
- 请求方与工具端连接均校验 token；服务端日志不会输出明文 token 或 server key。
- `allowed_origins` 支持逗号分隔多个来源；设置为 `*` 时自动关闭 CORS credentials。
- token 已经过 URL 编码，接入时不要再次编码；AES-ECB 为兼容既有 token 协议保留。

## Docker 部署

镜像发布在 Docker Hub：`kid941005/mcp-endpoint-server:latest`，版本标签如 `0.0.7`。

```bash
docker pull kid941005/mcp-endpoint-server:latest
docker compose up -d
```

- `data/` 保存 `.mcp-endpoint-server.cfg`（含 server.key 与加密 token），`logs/` 保存日志。
- 容器以非 root 用户 `appuser`（uid 1000）运行，首次部署前确保目录可写：
  `mkdir -p data logs && sudo chown -R 1000:1000 data logs`。
- 镜像由 GitHub Actions（`.github/workflows/docker-publish.yml`）在推送 `main` 与 `v*` 标签时构建并推送，
  同时推送 `latest` 与版本号标签；需要仓库 Secrets `DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`。

## 测试

本地运行后端单元测试：

```bash
python -m pip install -r requirements-dev.txt
python -m coverage run -m unittest discover -s tests -p 'test_*.py' -v
python -m coverage report -m
```

覆盖率配置见 `.coveragerc`，CI 以 75% 为最低门槛；当前核心测试覆盖率约 79%。
mcp-bridge 的测试单独运行，详见 [mcp-bridge/README.md](./mcp-bridge/README.md)。

## 技术细节
[技术细节](./README_dev.md)

