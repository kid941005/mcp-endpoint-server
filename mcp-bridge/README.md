# MCP Bridge

把 **stdio / SSE / Streamable HTTP** 三种 MCP Server 统一接入
[`mcp-endpoint-server`](../README.md) 的工具端。

```text
stdio MCP Server  ─┐
SSE MCP Server    ─┼──> mcp-bridge ──WebSocket──> mcp-endpoint-server ──> 小智/设备
HTTP MCP Server   ─┘
```

## 为什么需要它

`mcp-endpoint-server` 只接受 WebSocket 工具端（`/mcp_endpoint/mcp/`），
本身不主动连接 MCP Server。Bridge 作为工具端连接 endpoint server，同时作为
MCP 客户端连接真正的 MCP Server，把 JSON-RPC 原样转发。

Bridge **不做 MCP 高层会话处理**：`initialize`、`tools/list`、`tools/call`、
通知等全部透传，所以协议版本由两端自行协商，兼容 MCP 2025-06-18（Server 2.3）。

## 兼容性

- **MCP SDK**：需要 mcp 2.3.x（`mcp>=2.3.0,<3`），HTTP 栈随之使用 `httpx2>=2.10,<3`。本组件已从 mcp 1.30 迁移完成，运行时不再支持 1.x。
- **协议版本**：bridge 不解析 `initialize`，由两端协商，实测兼容 MCP `2025-06-18`。
- **2.x 迁移带来的变化**（自行改代码或替换依赖时注意）：
  - Streamable HTTP 的 headers 与超时在 2.x 改由内部 HTTP 客户端承载，环境变量不变（`MCP_HEADERS`、`MCP_TIMEOUT` 行为一致）。
  - 2.x 的 `JSONRPCMessage` 是联合类型、没有 `model_validate`，bridge 改用 `mcp.types.jsonrpc_message_adapter` 校验；上游返回 `result: null` 的响应仍按原始 JSON 透传，不会被丢弃。
  - 2.x 把 `FastMCP` 更名为 `mcp.server.mcpserver.MCPServer`，`host`/`port` 移到 `run()`；自带的 MCP Server 若用旧路径需按 2.x 文档调整（bridge 自身不受影响）。
- **测试替身**：`tests/fake_fastmcp_server.py` 对 1.x / 2.x 自适应，方便在旧环境跑测试。
- **启动日志**：启动首行打印 bridge 版本与依赖版本（mcp / httpx2 / websockets），`--print-config` 的 JSON 也包含 `version`，便于核对容器里实际运行的版本。

## 快速开始

### 1. 从 endpoint server 获取工具端地址

启动 `mcp-endpoint-server` 后，日志会打印：

```text
单模块部署MCP接入点: ws://<ip>:8004/mcp_endpoint/mcp/?token=...
```

这个地址填入 `MCP_ENDPOINT`。token 已经过 URL 编码，**不要再次编码**。

### 2. stdio

stdio 模式需要 bridge 启动真正的 MCP Server。Dockerfile 和示例 compose 只包含 bridge，
不包含 MCP Server 代码和依赖；请先把 server 加入镜像或挂载到容器，或直接在宿主机运行：

```bash
python -m pip install -r requirements.txt
export MCP_ENDPOINT='ws://127.0.0.1:8004/mcp_endpoint/mcp/?token=...'
export MCP_TRANSPORT=stdio
export MCP_COMMAND=python
export MCP_ARGS='["/app/server.py"]'
python mcp_bridge.py
```

Docker 方式见下方“Docker 运行”。

### 3. SSE

```bash
export MCP_TRANSPORT=sse
export MCP_URL='http://127.0.0.1:8080/sse'
export MCP_HEADERS='{"Authorization":"Bearer xxx"}'
python mcp_bridge.py
```

### 4. Streamable HTTP

```bash
export MCP_TRANSPORT=streamable-http
# 也接受 http / streamablehttp 别名
export MCP_URL='http://127.0.0.1:8080/mcp'
python mcp_bridge.py
```

## Docker 运行

`docker-compose.example.yml` 只运行 bridge 本身：

```bash
cp .env.example .env
# 编辑 .env
docker compose -f docker-compose.example.yml up -d --build
```

- **SSE / Streamable HTTP**：只需在 `.env` 设置 `MCP_TRANSPORT` 和 `MCP_URL`。
- **stdio**：需要把 MCP Server 的代码和依赖加入镜像或挂载到容器，否则
  `MCP_COMMAND` 找不到 server；镜像本身只包含 `mcp_bridge.py`。
- **endpoint server 也在 Docker 中**：`.env` 的 `MCP_ENDPOINT` 使用
  `ws://host.docker.internal:8004/mcp_endpoint/mcp/?token=...`（compose 已配置
  `extra_hosts` 兼容 Linux），或让两个容器加入同一 Docker 网络。

## 配置

| 变量 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `MCP_ENDPOINT` | 是 | - | endpoint server 工具端 WebSocket URL |
| `MCP_TRANSPORT` | 否 | `stdio` | `stdio` / `sse` / `streamable-http`（`http` 为别名） |
| `MCP_SERVER_NAME` | 否 | `mcp` | 日志名称 |
| `MCP_COMMAND` | stdio 是 | - | 可执行文件，如 `python`、`node` |
| `MCP_ARGS` | 否 | `[]` | JSON 数组优先，也支持 shell 风格字符串；Windows 路径必须用 JSON 数组（shell 解析会把反斜杠当转义） |
| `MCP_CWD` | 否 | 当前目录 | stdio 子进程工作目录 |
| `MCP_ENV` | 否 | - | JSON 对象，追加到子进程环境变量；子进程默认继承 bridge 进程的全部环境变量（含其中的密钥），需要隔离请用干净的父环境启动 bridge |
| `MCP_URL` | SSE/HTTP 是 | - | MCP Server URL |
| `MCP_HEADERS` | 否 | `{}` | JSON 对象，HTTP 请求头 |
| `MCP_TIMEOUT` | 否 | `30` | 连接/HTTP 超时秒数 |
| `MCP_SSE_READ_TIMEOUT` | 否 | `300` | SSE 读超时秒数 |
| `MCP_HTTP_TRUST_ENV` | 否 | `0` | 是否读取 `HTTP_PROXY`/`NO_PROXY`；默认关闭，避免畸形 `NO_PROXY` 让 HTTP 客户端启动失败 |
| `MCP_MAX_SIZE` | 否 | `16777216` | WebSocket 最大消息字节；`0` 表示不限制 |
| `MCP_RECONNECT_INITIAL` | 否 | `1` | 重连初始秒数 |
| `MCP_RECONNECT_MAX` | 否 | `60` | 重连最大秒数 |
| `MCP_PING_INTERVAL` | 否 | `20` | WebSocket ping 间隔；`0` 关闭 |
| `MCP_PING_TIMEOUT` | 否 | `20` | WebSocket ping 超时；`0` 关闭 |
| `MCP_LOG_LEVEL` | 否 | `INFO` | `DEBUG` 可看到每条转发消息 |

查看脱敏后的配置：

```bash
python mcp_bridge.py --print-config
```

## 多 MCP Server

一个 bridge 进程只负责一个 MCP Server。要接多个，启动多个 bridge 容器/进程，
每个使用自己的 `MCP_ENDPOINT`（同一 token 也可以）和 `MCP_SERVER_NAME`。

endpoint server 会按工具名路由 `tools/call`，多个 bridge 可以同时连接同一个
`/mcp_endpoint/mcp/`。

## 测试

```bash
cd mcp-bridge
python -m unittest discover -s tests -p 'test_mcp_bridge.py' -v
```

测试覆盖配置解析、stdio 端到端、SSE 端到端和 Streamable HTTP 端到端；
后两者会启动一个真实的 MCP 测试服务（mcp 2.x 的 `MCPServer`）。

## 注意

- `/mcp/` 和 `/call/` 必须使用同一个 token/agentId，否则会提示工具端未连接。
- stdio 子进程的 stdout 只能输出 MCP JSON-RPC；日志请写 stderr。
- stdio 子进程会继承当前进程的完整环境变量，`MCP_ENV` 只做追加；不想让 MCP Server 看到宿主密钥时，用最小化环境启动 bridge。
- `MCP_ARGS` 的 shell 风格字符串在 Windows 上可能吃掉反斜杠路径，建议始终写 JSON 数组。
- SSE 是旧传输，优先使用 Streamable HTTP。
- 日志不会打印 token；`--print-config` 也会隐藏 endpoint 的 token、headers 值，以及 `MCP_ARGS` 里 `--token`/`--key`/`--secret`/`--password` 类参数的值（`--token xxx` 与 `--token=xxx` 都覆盖）。
- SSE / Streamable HTTP 默认不读取代理环境变量；需要走 `HTTP_PROXY` 时设置 `MCP_HTTP_TRUST_ENV=1`。
