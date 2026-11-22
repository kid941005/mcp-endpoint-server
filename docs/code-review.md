# mcp-endpoint-server 代码审查报告

- 审查基线：kid941005/mcp-endpoint-server `main`（审查时 [`e9e48639`](https://github.com/kid941005/mcp-endpoint-server/commit/e9e48639f7e2c7f7565dd54f09d4b82bc9550dcd)，落地提交 [`4dca5650`](https://github.com/kid941005/mcp-endpoint-server/commit/4dca5650c89c45eaba6e0b28526194211b480e1d)（7 files / +241 −101）与去重补丁 [`e36ef14`](https://github.com/kid941005/mcp-endpoint-server/commit/e36ef144d79a416f9bcad537378d7631552ddb31)（2 files / 同名工具去重）与 header 鉴权 [`604a6881`](https://github.com/kid941005/mcp-endpoint-server/commit/604a688146cdd46357f30c56dc6ad6d023be892c) + 测试修正 [`fd1d22d2`](https://github.com/kid941005/mcp-endpoint-server/commit/fd1d22d2394bf9ff66a01658b78bb2e978d1409b)（3 files / server.key 改走 Authorization 头）与双工具端 e2e [`d6ae8e5`](https://github.com/kid941005/mcp-endpoint-server/commit/d6ae8e56d230dd43ebdc473e75b700c29e828539)（3 files / 聚合路径跨服务器去重 + 双工具端 e2e 测试）与收尾补丁 [`7077bab`](https://github.com/kid941005/mcp-endpoint-server/commit/7077babad265ede638a1622af67aa7b7694c5ea0)（7 files / 死代码清理 + 导入副作用、栈深与外网探测修复）与 N1–N4 修复 [`8718b78`](https://github.com/kid941005/mcp-endpoint-server/commit/8718b787b0a81d8e41ea7a916ab8734a5eb0c06e)（8 files / 服务器主动消息转发、wire 序列化保留 null result、只读 peek 消除导入期落盘）与 bridge 测试修正 [`d846699`](https://github.com/kid941005/mcp-endpoint-server/commit/d846699e585ecfaccfdd205710a3ca05e1615da0)（1 file）与 bridge 轻微项收尾 [`2aed70f`](https://github.com/kid941005/mcp-endpoint-server/commit/2aed70fe60ae9f089320d844ccd30637ccd45825)（8 files / B5、B6、B8–B12））
- 审查对象：约 2,700 行 Python（主服务 ~1,700 行 + bridge 485 行）+ 测试 + Docker/Compose + 4 个 workflow
- 方法：全量通读源码/测试/文档/workflow；静态调用链核对；`python -m py_compile` 全量语法校验通过
- 测试执行：本机缺 fastapi 等依赖（pip 安装超时），改用 loguru stub 跑通 `tests.test_core`/`test_handlers`/`test_forwarding_lock` 共 23 项；完整套件由 CI 验证
- 行号以审查基线为准；已落地的修改会使后续行号小幅偏移

## 落地状态（提交 `4dca5650` + `e36ef14` + `fd1d22d` + `d6ae8e5` + `7077bab` + `8718b78` + `d846699` + `2aed70f`，2026-11-22）

| 编号 | 问题 | 状态 |
|---|---|---|
| S1 | 多服务器 initialize/ping 返回非法 result | ✅ 已修：`PendingResponse.method` + 显式分派，非 tools 多响应返回首个合法 result 并告警；新增回归测试 |
| M1 | 无 id 通知被回错误包 | ✅ 已修：无连接 / 转发失败 / 全部失败 / 内部异常四条路径均加 `request_id is not None` 守卫 |
| M2 | `decrypt`、`validate_token_and_get_agent_id` 注解与实现不符 | ✅ 已修：改 `Optional[str]` |
| M3 | health/stats 校验重复；key 走 GET query | ✅ 已修（`604a6881` + `fd1d22d`）：收敛为 `_is_authorized_request`，key 改走 `Authorization: Bearer`（头优先），query 兼容保留一个周期；补 5 项测试 |
| M4 | 同名工具跨服务器冲突 | ✅ 已修（`e36ef14`）：`_select_tool_connections` 统一 list/call 选择键、按名去重、`_meta` 暴露被遮蔽连接、`/stats` 增 `tool_conflicts`；补 4 项回归测试。客户端 `tools/list` 聚合链路的去重见 M8（`d6ae8e5`） |
| M8 | `tools/list` 聚合路径未去重（M4 未覆盖的客户端链路） | ✅ 已修（`d6ae8e5`）：`aggregate_responses` 按连接 UUID 排序、按工具名首次保留并附 `_meta.shadowed_by`；补 1 项单测 + 3 项双工具端 e2e |
| M5 | pending 覆盖时未取消旧超时任务 | ✅ 已修：覆盖前 `timeout_task.cancel()`；新增回归测试 |
| M6 | 无优雅停机 | ✅ 已修：新增 `ConnectionManager.shutdown()`，lifespan 关闭时调用 |
| M7 | 工具调用错误回包四段重复 | ✅ 已修：收敛为 `_reply_error`（`websocket_handler.py:24`） |
| L1 | 死状态字段只写不读 | ✅ 已修：删除 `connection_timestamps` 与未用 `timestamp` |
| L2 | `create_authentication_error` 无调用方 | ✅ 已修（`7077bab`）：删除死函数，管理接口鉴权失败响应收敛为 `server._authentication_error_response()` |
| L3 | `sys._getframe(6)` 硬编码栈深 | ✅ 已修（`7077bab`）：改为从当前帧向上跳过 `logging` 与本文件帧，不再依赖固定深度 |
| L4 | 启动探测 8.8.8.8 | ✅ 已修（`7077bab`）：结果缓存 + 探测目标改 RFC 5737 `192.0.2.1:9`，不再依赖公网可达 |
| L5 | config `print` / 导入副作用 | ✅ 已修（`7077bab`）：`ConfigManager` 首次取值才加载/创建；`print` 改为 `generated_key` 标记，由启动流程 `logger.warning`。注意 `src.utils.logger` 导入即实例化 `LoggerManager` 并读配置，应用入口导入仍会加载配置（见第七节 N4） |
| L6 | README_dev 结构与版本不一致 | ✅ 已修：目录树补齐 5 个模块，conda python 3.10 → 3.12 |
| L7 | 宽泛 `except Exception` 吞异常 | ✅ 已修（`7077bab`）：WS 循环、工具列表/初始化响应、错误回包失败四处改 `logger.exception` 保留堆栈；AES 异常已收窄；顶层兜底保留 |
| L8 | 未鉴权连接先完成 WS 握手 | ✅ 已修：先 `validate_token_and_get_agent_id` 再 `accept` |

### CI 结果（提交 `4dca5650`，2026-11-22 06:14Z 触发）

| Workflow | 触发条件 | 结果 | Run | 耗时 |
|---|---|---|---|---|
| `core tests` | push（core-tests.yml，矩阵 py3.11 / py3.12） | ✅ success | [44992805104](https://github.com/kid941005/mcp-endpoint-server/actions/runs/44992805104) | 20s（06:14:08Z → 06:14:28Z） |
| `docker publish` | push main（docker-publish.yml） | ✅ success | [44992805149](https://github.com/kid941005/mcp-endpoint-server/actions/runs/44992805149) | 62s（06:14:08Z → 06:15:10Z） |
| `mcp-bridge tests` | 未触发：mcp-bridge-tests.yml 仅 `paths: mcp-bridge/**`，本次未改该目录 | — | — | — |
| `Docker Image CI` | 未触发：docker-image.yml 仅 tag `v*` 与 workflow_dispatch | — | — | — |

`core tests` 实际执行 `python -m compileall -q main.py src` + `coverage run -m unittest discover -s tests -p 'test_*.py'`（覆盖 test_core / test_handlers / test_utils / test_server / test_integration，覆盖率门槛 75%），Python 3.11 与 3.12 双矩阵均通过；workflow 已使用 `actions/checkout@v7`、`actions/setup-python@v7`。

提交改动量：`connection_manager.py +78/−16`、`websocket_handler.py +82/−63`、`server.py +23/−14`、`aes_utils.py +7/−4`、`README_dev.md +12/−4`、`test_core.py +22`、`test_handlers.py +17`。

### CI 结果（去重补丁 `e36ef14`）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `core tests` | push（py3.11 / py3.12 矩阵，覆盖 test_core 含新增 4 项） | ✅ success | [45000659306](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45000659306) |
| `docker publish` | push main | ✅ success | [45000659171](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45000659171) |
| `mcp-bridge tests` / `Docker Image CI` | 未触发：paths / tag 条件未满足 | — | — |

### CI 结果（header 鉴权 `604a6881` → 测试修正 `fd1d22d`）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `core tests` | push（py3.11 / py3.12 矩阵，覆盖 test_server 新增 5 项） | ❌ `604a6881` failure（`test_stats_still_accepts_query_key_for_compat` 断言错字段）→ ✅ `fd1d22d` success | [45001161157](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45001161157) / [45001339284](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45001339284) |
| `docker publish` | push main | ✅ success | [45001339310](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45001339310) |
| `mcp-bridge tests` / `Docker Image CI` | 未触发：paths / tag 条件未满足 | — | — |

### CI 结果（双工具端 e2e `d6ae8e5`，2026-11-22 08:17Z）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `core tests` | push（py3.11 / py3.12 矩阵，含新增 3 项双工具端 e2e） | ✅ success（两矩阵均通过，日志确认 3 项 `DualToolServerRelayTests` 全部 ok） | [45002084587](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45002084587) |
| `docker publish` | push main | ✅ success | [45002084572](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45002084572) |
| `mcp-bridge tests` / `Docker Image CI` | 未触发：paths / tag 条件未满足 | — | — |

### CI 结果（收尾补丁 `7077bab`，2026-11-22 08:25Z）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `core tests` | push（py3.11 / py3.12 矩阵，含新增 2 项 utils 用例） | ✅ success（两矩阵均 `Ran 56 tests`，coverage 80% > 75% 门槛） | [45002721485](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45002721485) |
| `docker publish` | push main | ✅ success（08:25:14Z → 08:26:14Z） | [45002721500](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45002721500) |
| `mcp-bridge tests` / `Docker Image CI` | 未触发：paths / tag 条件未满足 | — | — |

### CI 结果（N1–N4 `8718b78` → bridge 测试修正 `d846699`，2026-11-22 09:02Z / 09:04Z）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `core tests` | push（py3.11 / py3.12 矩阵，含 peek 2 项与服务器主动消息 4 项） | ✅ success | [45005555591](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45005555591) |
| `mcp-bridge tests` | push（paths: mcp-bridge/**） | ❌ failure（`8718b78`）：3 项 wire 用例构造了 SDK 不允许的 `result:null` → `ValidationError`；`d846699` 改用替身对象后 ✅ success | [45005555984](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45005555984) → [45005752789](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45005752789) |
| `docker publish` | push main | ✅ success（两次提交均通过） | [45005555407](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45005555407) / [45005752798](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45005752798) |
| `core tests` / `Docker Image CI`（`d846699`） | 未触发：只改 `mcp-bridge/tests/**` | — | — |

### CI 结果（bridge 轻微项收尾 `2aed70f`，2026-11-22 10:00Z）

| Workflow | 触发条件 | 结果 | Run |
|---|---|---|---|
| `mcp-bridge tests` | push（paths: `mcp-bridge/**`，含新增 ShellArgs 3 / EnvIsolation 1 / Healthcheck 3 / ReconnectBackoff 1 / TestDoubleGuard 1，共 9 项） | ✅ success（全套 35 项） | [45010332564](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45010332564) |
| `docker publish` | push main（镜像构建同时验证 `HEALTHCHECK` 语法） | ✅ success（10:01:55Z 完成） | [45010332608](https://github.com/kid941005/mcp-endpoint-server/actions/runs/45010332608) |
| `core tests` / `Docker Image CI` | 未触发：本次只改 `mcp-bridge/**`；tag 条件未满足 | — | — |

此前 B 系列各提交的 CI（均 ✅）：`140d279` run 45006954669 / 45006954674，`cc144e00` run 45007976904 / 45007976865，`40a4f56` run 45009512285 / 45009512312，`55f2b19` run 45009751856 / 45009751828；compose 增强 `fbe2b5e` run 45011333703（mcp-bridge tests）/ 45011333655（docker publish）均 ✅；autoheal `4af9d10` run 45011715054（mcp-bridge tests）/ 45011715087（docker publish）均 ✅。

## 一、结构与分层

| 层 | 文件 | 职责 | 行数 |
|---|---|---|---|
| 入口 | `main.py` | 启动 uvicorn | 6 |
| 接入层 | `src/server.py` | HTTP 路由、WS 端点、token 鉴权、lifespan | 267 |
| 核心状态层 | `src/core/connection_manager.py` | 连接注册、ID 变换、待决响应聚合、工具索引、统计 | 507 |
| 协议编排层 | `src/handlers/websocket_handler.py` | 工具端/智能体端消息处理、广播与聚合转发 | 344 |
| 基础设施 | `src/utils/{config,aes_utils,jsonrpc,logger,util}.py` | 配置/加密/JSON-RPC/日志/网络工具 | 100/127/237/145/13 |
| 传输桥 | `mcp-bridge/mcp_bridge.py` | stdio / sse / streamable-http 与 WS 互转 | 485 |
| 测试 | `tests/*.py`、`mcp-bridge/tests/*.py` | 单元 + 端到端 | ~900 |
| 部署 | `Dockerfile`、`docker-compose.yml`、`.github/workflows/*` | 构建/发布/CI | — |

分层评价：
- 依赖方向单向（server → handler → core → utils），**无循环依赖**。
- 职责基本清晰，但 `connection_manager` 一个类承担五种职责（507 行），是唯一的高耦合点。
- handler 与 manager 通过模块级单例直接耦合，单测需构造全局状态，隔离性偏弱。

## 二、架构评估（可扩展/可维护 vs 最佳实践）

优点：
1. 单进程 asyncio 状态机，模型简单，无外部中间件。
2. 请求 ID 变换 `{conn_uuid}_n_{id}` / `_s_{id}` 保证跨连接唯一，`_restore_jsonrpc_id` 兼容数字与字符串 id，且有测试。
3. 聚合器错误优先返回，避免把 error 混进 tools/content 产生非法 MCP 结果。
4. bridge 支持三种传输，`from_env` 校验完整，URL 脱敏，断线指数退避。
5. 部署规范：Docker 多阶段 + 非 root + healthcheck；CI 覆盖率门槛 `fail_under=75`。

不足：
1. 多服务器聚合语义不完整（S1）。
2. 全局单例（`config` / `connection_manager` / `websocket_handler`）不利于替换与并发测试。
3. 无优雅停机：lifespan 只记录日志，不关闭 WS、不取消 pending 超时任务。
4. 无请求级限流/背压；pending 超时固定 30s，不可配置。
5. 无结构化指标导出，`/stats` 仅内存计数。

## 三、功能完整性

| 功能 | 状态 | 证据 / 缺口 |
|---|---|---|
| token 鉴权 | 已完成 | `server.py:27-60`，AES 解密 + agentId |
| MCP 工具端 WS | 已完成 | `/mcp_endpoint/mcp/` |
| 智能体端 WS | 已完成 | `/mcp_endpoint/call/` |
| tools/list 聚合 | 已完成 | handler + `aggregate_responses`；跨服务器同名工具按 UUID 序去重并附 `_meta`（M8） |
| tools/call 路由 | 已完成 | `find_tool_connection` 单服务器路由 |
| initialize（单服务器） | 已完成 | `len==1` 直通 |
| initialize / ping（多服务器） | 已完成 | S1 已修：非 tools 多响应返回首个合法 result |
| 通知转发 | 已完成 | 但无连接时错误回复违规（M1） |
| 待决响应超时 | 已完成 | 30s 统一 `-32002` |
| 健康检查 / 统计 | 已完成 | `Authorization: Bearer` 鉴权，query 兼容保留一个周期（M3） |
| 工具列表热更新 | 已完成 | `update_tool_list` |
| 优雅停机 | **缺失** | M6 |
| 限流 / 背压 | **缺失** | 待补充：是否由前置网关承担 |

## 四、问题清单

### 严重

**S1 多服务器 initialize/ping 返回非法 result 结构**
- 位置：`src/core/connection_manager.py:373-376`（`aggregate_responses`）
- 问题：广播给该 agent 全部 MCP 连接后，非 tools/content 的多条成功响应走 `else`，返回 `result={"responses":[...]}`。MCP 的 `initialize` 要求 result 含 `protocolVersion/capabilities/serverInfo`，`ping` 要求 result 为 `{}`；≥2 个 MCP 服务器时两者都被破坏。
- 影响：README 宣称的“一个智能体连接多个 MCP 服务器”在握手阶段即失败，多服务器能力实际不可用。单服务器（`len==1`）正常，`tests/test_integration.py` 只连 1 个工具端，故 CI 未暴露。
- 修复方案（行为变更，需补测试）：
  1. 给 `PendingResponse` 增加 `method` 字段，由 `register_pending_response(..., method=...)` 传入；
  2. `aggregate_responses` 显式分派：tools/list → 合并 tools；tools/call → 合并 content；其余（initialize/ping/未知）→ 返回 `successful[0]`，不伪造 `responses`；
  3. 若确需保留全部子响应，放到 `result._meta.responses`，不改写标准字段；
  4. 新增测试：两个 mock 工具端，断言 initialize/ping 的 result 为单对象且字段合法。
- 状态：**已修（4dca5650）**：按方案 1/2 实现（`method` 字段 + 显式分派，非 tools 多响应返回 `successful[0]` 并告警）；新增 `test_multiple_non_tools_responses_keep_single_result`。方案 3（`_meta.responses`）未采用。

### 中等

**M1 通知（notification）被回复错误，违反 JSON-RPC 2.0**
- 位置：`src/handlers/websocket_handler.py:151-156`
- 问题：无 `id` 的通知在 agent 无 MCP 连接时仍发送 `tool_not_connected` 错误（`request_id=None`）。
- 影响：客户端收到 id 为 null 的意外响应；严格实现会判协议错误。
- 处理：仅 `request_id is not None` 时回错误；顺带消除重复的 `get_agent_mcp_connections` 调用。
- 状态：**已修（4dca5650）**：行为变更，符合 JSON-RPC 2.0。

**M2 类型注解与实现不符**
- 位置：`src/utils/aes_utils.py:62` `decrypt(...) -> str`；`src/server.py:27` `validate_token_and_get_agent_id(...) -> str`
- 问题：两者失败路径均 `return None`。
- 影响：mypy/IDE 误判；调用方可能漏判 None。
- 处理：改为 `Optional[str]`（已加 `from typing import Optional`）。
- 状态：**已修（4dca5650）**：零行为变更。

**M3 health/stats 的 key 校验重复，且 key 走 GET query**
- 位置：修复前 `src/server.py:123-162`；修复后 `src/server.py:127-170`（helper）、`172-197`（health）、`199-213`（stats）
- 问题：两处 `hmac.compare_digest` 逻辑逐字重复；`?key=` 会进入反向代理日志/浏览器历史（应用侧已 `access_log=False`，风险主要在前置代理与 URL 留存）。
- 影响：改校验规则需改两处，易漏；密钥经 URL 传播面偏大。
- 处理（已按此实施）：
  1. `_extract_authorization_key(authorization)`（第 127 行）：解析 `Authorization` 头，同时接受 `Bearer <key>` 与裸密钥两种写法，大小写不敏感；
  2. `_is_authorized_request(authorization, key)`（第 143 行）：**头优先**——头存在则只认头，头错误直接拒绝（不降级到 query，避免绕过）；两者都走 `hmac.compare_digest` 常量时间比较；
  3. query `key` 继续可用，首次命中打印一条 `logger.warning` 废弃提示（进程内只打一次，不刷日志），下个发布周期移除；
  4. 错误响应结构与错误码（`-32004`）保持不变，调用方无需改错误处理。
- 状态：**已实施（`604a6881` + `fd1d22d`）**：两个端点改为 `authorization: Optional[str] = Header(default=None)`；新增 5 项测试（Bearer / 裸密钥 / 错误密钥 / 头优先于 query / query 兼容），原 query 用例保留为兼容回归。`604a6881` 首跑 `core tests` 因 stats 兼容用例断言错字段失败，`fd1d22d` 修正后双矩阵通过。

**M4 同名工具跨服务器冲突（列表与调用选择键不一致）**
- 位置：修复前（`4dca5650`）`src/core/connection_manager.py:435-442`（`get_all_tools` 不去重）、`444-458`（`get_agent_tools_summary` 按名去重、保留**连接注册顺序**首个）、`460-477`（`find_tool_connection` 按 **UUID 排序**取首个）、`411-426`（注册时告警）；修复后（`e36ef14`）对应实现见下节行号
- 问题：两个 MCP 服务器暴露同名工具时——
  1. `get_all_tools` 直接拼接各连接的工具，返回重复条目；
  2. 更关键：`get_agent_tools_summary` 与 `find_tool_connection` 用了**不同的选择键**（dict 插入序 vs UUID 字典序）。当连接注册顺序与 UUID 排序不一致时，客户端在 `tools/list` 看到的同名工具，与 `tools/call` 实际路由到的服务器**可能不是同一台**。
- 影响：多服务器场景下调用可能打到非预期服务器；"列表展示"与"真实路由"自相矛盾，排障时现象难以复现。
- 去重方案（**保持客户端工具名不变，不加前缀**；已按此实施）：
  1. **统一选择键**：新增私有方法 `_select_tool_connections(agent_id, tool_name) -> List[str]`，返回 `sorted(匹配的连接 UUID)`；`tools/list` 去重与 `tools/call` 路由都只调它，保证"看到的就是会调到的"。
  2. **列表去重**：`get_agent_tools_summary` 改为按该排序遍历连接、工具名首次出现即保留（输出仍按工具名 `sorted()`，对客户端顺序不变）；`get_all_tools` 同样按名去重，消除重复条目。
  3. **冲突信息放 `_meta`**：保留的工具对象浅拷贝后加
     `"_meta": {"mcp-endpoint-server": {"shadowed_by": ["<uuid>", ...]}}`，
     不改 `name` / `description` / `inputSchema`。MCP 允许客户端忽略未知 `_meta`，对现有客户端零影响，仅让运维可追溯被遮蔽的连接。
  4. **可观测性**：`/stats` 增加 `tool_conflicts: [{agent_id, tool, uuids}]`；注册告警文案与排序语义对齐（"UUID 最小者胜出"）。
  5. **测试**：两个 mock 工具端注册同名工具，断言 (a) `tools/list` 中该名只出现一次；(b) `tools/call` 打到 UUID 最小的连接；(c) `_meta.shadowed_by` 列出被遮蔽连接；(d) 注册顺序颠倒后路由结果不变。
- 状态：**已实施（`e36ef14`）**——`_select_tool_connections()` 成为 list/call 唯一选择键；`get_all_tools` 按名去重；`get_agent_tools_summary` 按同一顺序取首个；被遮蔽工具附 `_meta.mcp-endpoint-server.shadowed_by`；`/stats` 新增 `tool_conflicts`；新增 4 项回归测试（列表唯一且与路由一致、注册顺序无关、无冲突不加 `_meta`、stats 暴露冲突）。列表与调用现在保证指向同一台服务器。

**M8 `tools/list` 聚合路径未去重（M4 的盲区）**
- 位置：`src/core/connection_manager.py:368-381`（`aggregate_responses` 的 tools 分支）
- 问题：M4 的去重只作用于 `get_all_tools` / `get_agent_tools_summary`（`/health?agentid`、`/stats` 与内部汇总）；客户端真正拿到的 `tools/list` 走的是“广播 → `aggregate_responses`”链路，该分支仍是 `tools.extend(result.get("tools") or [])` 原样拼接。两个工具端暴露同名工具时，客户端会收到重复工具名，且保留的那条描述与 `tools/call` 实际打到的服务器（UUID 最小者）可能不是同一台。
- 影响：M4 在真实客户端链路上等于未生效；`tests/test_integration.py` 只连 1 个工具端，故长期未被发现。
- 修复（已按此实施）：`aggregate_responses` 改为遍历 `sorted(pending.received_responses)`（键即 MCP 连接 UUID），按工具名首次出现保留，被遮蔽连接写入 `_meta["mcp-endpoint-server"]["shadowed_by"]`，输出按工具名排序——与 `get_all_tools` 完全同语义，且无需给 `PendingResponse` 增加 `agent_id`；无 `name` 的条目原样保留。
- 状态：**已实施（`d6ae8e5`）**：单测 `test_duplicate_tools_keep_lowest_uuid_and_meta`（同名工具去重、取 UUID 最小者的 description、`_meta` 列出被遮蔽 UUID）；双工具端 e2e 3 项覆盖真实 WS 链路。

**M5 待决响应覆盖时未取消旧超时任务**
- 位置：`src/core/connection_manager.py:262-271`
- 问题：`register_pending_response` 直接覆盖 `self.pending_responses[tid]`，同一 `tid` 重复注册时旧 `timeout_task` 未 cancel；旧任务 30s 后 `pop` 会取走**新** pending 并给机器人发超时错误。
- 影响：ID 复用/重试场景下误报超时、旧任务泄漏。
- 建议：注册前先 `old = self.pending_responses.get(tid)`，存在则 `old.timeout_task.cancel()`；补“重复注册”单测。
- 状态：**已修（4dca5650）**：注册前 cancel 旧任务，新增 `test_reregister_cancels_previous_timeout_task`。

**M6 无优雅停机**
- 位置：`src/server.py:63-77`（lifespan）
- 问题：关闭时只记录日志，不关闭全部 WS、不 cancel pending 超时任务、不清空连接表。
- 影响：测试/热重载出现 “Task was destroyed but it is pending”；容器滚动更新时客户端收到硬断连而非 close 帧。
- 建议：shutdown 分支遍历连接 `await ws.close(code=1001)`，并 `for p in pending: p.timeout_task.cancel()`；加 lifespan 测试。
- 状态：**已修（4dca5650）**：新增 `ConnectionManager.shutdown()` 并在 lifespan 调用；未加 lifespan 专项测试（仅由进程启停路径间接覆盖）。

**M7 `_handle_tool_call_request` 四段错误处理重复**
- 位置：`src/handlers/websocket_handler.py:253-266 / 271-281 / 284-294 / 316-329`
- 问题：四段均为“构造 error_response → forward_to_robot → return”，仅 code/message 不同。
- 影响：新增校验分支需复制模板，易漏清理 pending。
- 建议：抽 `async def _reply_error(connection_uuid, request_id, code, message)`；或统一 `raise RPCError` + 外层 `except` 统一回包（同时消除 333-343 的兜底重复）。
- 状态：**已修（4dca5650）**：采用第一种方案，`_reply_error` 位于 `websocket_handler.py:24`，5 处错误回包已统一。

### 轻微

| 编号 | 位置 | 问题 | 建议 | 状态 |
|---|---|---|---|---|
| L1 | `connection_manager.py:59,80,91`；`PendingResponse.timestamp`、`RobotConnection.timestamp` | 死状态字段只写不读 | 删除或接入 `/stats` 展示连接时长 | ✅ 已修：删除 `connection_timestamps` 与未用 timestamp |
| L2 | `src/utils/jsonrpc.py:232` | `create_authentication_error` 无调用方 | 删除或让 server 改用 | ✅ 已修（`7077bab`）：删除死函数；server 侧新增 `_authentication_error_response()` 统一两处错误体 |
| L3 | `src/utils/logger.py:27` | `sys._getframe(6)` 硬编码栈深度，仅捕 `ValueError` | 改 `logger.opt(depth=...)` 或同时捕 `IndexError` | ✅ 已修（`7077bab`）：从 `logging.currentframe()` 向上跳过 `logging`/本文件帧，深度动态计算 |
| L4 | `src/utils/util.py:8-12` | 启动时 connect `8.8.8.8:80` 外网探测 | 惰性调用/网卡枚举，显式设短超时 | ✅ 已修（`7077bab`）：`gethostbyname` 优先，失败再 UDP connect `192.0.2.1:9`（0.2s 超时），结果进程内缓存 |
| L5 | `src/utils/config.py:127`；`config.py:60` | import 即创建 `data/*.cfg`；`print` 不受日志控制（内容不含密钥，安全上可接受） | 惰性单例/显式 `init_config()`；print 换 `logger.info` | ✅ 已修（`7077bab`）：`_ensure_loaded()` 延迟加载；`print` → `generated_key` 标记，lifespan 内 `logger.warning`。**残余**：`logger.py:141` 的 `LoggerManager()` 仍在导入时读配置，故整机导入链仍有一次配置加载（第七节 N4） |
| L6 | `README_dev.md` | 结构图缺 `jsonrpc.py`/`aes_utils.py`/`util.py`/`handlers/`；写 `python=3.10`，CI 为 3.11/3.12、Docker 3.12 | 补齐目录树，统一表述“3.10+，CI 覆盖 3.11/3.12” | ✅ 已修：目录树补齐 5 个模块，python 3.10 → 3.12 |
| L7 | `aes_utils.py:77-97`；`websocket_handler.py:333-343`；`server.py` WS 循环 | 宽泛 `except Exception` 吞异常，排障困难；循环内 `break` 静默断开 | 捕获具体异常并 `exc_info=True`；非连接类异常记录后继续 | ✅ 已修（`7077bab`）：四处 `logger.error` → `logger.exception`（WS 循环、tools/list 响应、initialize 响应、错误回包失败），断连仍保留 `break` 并注明原因；AES 异常已收窄；纯顶层兜底保留 |
| L8 | `src/server.py:168-171`、`203-206` | 未鉴权连接先完成 WS 握手再 `close(1008)` | 先校验 token，失败直接 `close` 不 accept；或限制握手超时 | ✅ 已修：先 validate 再 accept（`server.py:180/214`） |

## 五、已实施修改（`4dca5650` + `e36ef14` + `fd1d22d` + `d6ae8e5` + `7077bab` + `8718b78` + `d846699` + `2aed70f` + `fbe2b5e` + `4af9d10`，2026-11-22）

提交范围：`4dca5650` 7 files / +241 −101；`e36ef14` 2 files（同名工具去重）；`604a6881` + `fd1d22d` 3 files（`src/server.py`、`tests/test_server.py`、`README.md`，header 鉴权 + 测试修正）；`d6ae8e5` 3 files（`src/core/connection_manager.py`、`tests/test_core.py`、`tests/test_integration.py`，聚合路径去重 + 双工具端 e2e）；`7077bab` 7 files（`src/utils/{util,logger,config,jsonrpc}.py`、`src/server.py`、`src/handlers/websocket_handler.py`、`tests/test_utils.py`，L2–L5/L7 收尾）；`2aed70f` 8 files（bridge 轻微项收尾 + 新增 `mcp-bridge/healthcheck.py`）；`fbe2b5e` 1 file（`mcp-bridge/docker-compose.example.yml`，`env_file` 改 `path/required: false` 并补镜像健康检查说明）；`4af9d10` 1 file（同一 compose 示例新增 `willfarrell/autoheal` 旁车 + `autoheal=true` 标签，解决 unhealthy 不重启）。

1. `src/core/connection_manager.py`
   - `PendingResponse.__init__(..., method: Optional[str] = None)` 新增 `method`；`register_pending_response(..., method=None)` 覆盖前 `previous.timeout_task.cancel()`（M5）
   - `aggregate_responses`：删除 `else` 的 `{"responses": successful}`，非 tools 多响应返回 `successful[0]` 并告警（S1）
   - `update_tool_list`：跨连接同名工具 `logger.warning`，文案为"tools/call 与 tools/list 均取 UUID 最小的连接"（M4）
   - 新增 `_select_tool_connections(agent_id, tool_name) -> List[str]`（第 435 行）：返回 `sorted` 连接 UUID，作为 `tools/list` 去重与 `tools/call` 路由的唯一选择键（M4，`e36ef14`）
   - 新增 `_with_shadow_meta(tool_info, connection_uuids)`（第 449 行）：仅在冲突时返回浅拷贝并写 `_meta["mcp-endpoint-server"]["shadowed_by"]`，不动 `name`/`description`/`inputSchema`（M4，`e36ef14`）
   - `get_all_tools`（第 464 行）：按 UUID 序遍历、工具名首次出现即保留、结果按工具名排序，消除重复条目（M4，`e36ef14`）
   - `get_agent_tools_summary`（第 477 行）：改为按同一顺序取首个（原为 dict 插入序），与路由结果一致（M4，`e36ef14`）
   - `find_tool_connection`（第 493 行）：改用 `_select_tool_connections`，保留歧义告警（M4）
   - `get_connection_stats`（第 506 行）：新增 `tool_conflicts: [{agent_id, tool, uuids}]`（M4，`e36ef14`）
   - `aggregate_responses` 的 tools 分支：按 `sorted(pending.received_responses)` 遍历、按工具名首次保留、被遮蔽连接附 `_meta.mcp-endpoint-server.shadowed_by`、输出按工具名排序（M8，`d6ae8e5`）
   - 删除死字段 `connection_timestamps`（声明 + 两处写入）（L1）
   - 新增 `async def shutdown()`：取消全部 `timeout_task`、清 pending、`close(code=1001)`、清空连接表（M6）
2. `src/handlers/websocket_handler.py`
   - 新增 `async def _reply_error(...)`（第 24 行），广播路径与工具调用共 5 处错误回包统一走该入口
   - 通知（无 id）在无连接 / `forward_failed` / 全部转发失败 / 最外层异常四条路径均加 `request_id is not None` 守卫
   - 两处 `register_pending_response` 传入 `message_data.get("method")`
   - 两处 `logger.error` → `logger.exception`
3. `src/server.py`
   - lifespan 关闭分支调用 `await connection_manager.shutdown()`（第 78 行）
   - 两个 WS 端点改为先 `validate_token_and_get_agent_id` 再 `accept`（第 180 / 214 行）
   - `validate_token_and_get_agent_id(...) -> Optional[str]`（M2）
   - `604a6881`：删除 `_is_authorized_key`，改为 `_extract_authorization_key`（第 127 行）+ `_is_authorized_request`（第 143 行）；`health_check`（第 172 行）与 `stats`（第 199 行）新增 `authorization: Optional[str] = Header(default=None)`，头优先、query 兼容并打一次性废弃告警（M3）
4. `src/utils/aes_utils.py`
   - `decrypt(...) -> Optional[str]`；`b64decode` 捕 `(binascii.Error, ValueError)`，`decrypt`/`unpad` 捕 `ValueError`
5. `README_dev.md`
   - 目录树补齐 `__init__.py`/`aes_utils.py`/`jsonrpc.py`/`util.py`/`mcp-bridge/`/`tests/`/`requirements-dev.txt`/`Dockerfile`；`conda ... python=3.10` → `3.12`
6. 新增回归测试（共 28 项）
   - `tests/test_core.py`：`test_multiple_non_tools_responses_keep_single_result`、`test_reregister_cancels_previous_timeout_task`
   - `tests/test_core.py` 新增 `ToolConflictTests`（4 项，`e36ef14`）：`test_list_and_call_pick_same_connection`（列表唯一 + 与路由同一 UUID + `_meta.shadowed_by`）、`test_registration_order_does_not_change_selection`、`test_no_conflict_keeps_tool_untouched`、`test_stats_reports_tool_conflicts`
   - `tests/test_handlers.py`：`test_notification_without_id_gets_no_reply_when_no_mcp`
   - `tests/test_core.py` 新增 1 项（`d6ae8e5`）：`test_duplicate_tools_keep_lowest_uuid_and_meta`（两条响应暴露同名工具 → 去重、保留 UUID 最小者的 `description`、`_meta.shadowed_by` 列出被遮蔽 UUID）
   - `tests/test_integration.py` 新增 `DualToolServerRelayTests`（3 项，`d6ae8e5`）：`test_initialize_returns_single_mcp_result`（两个工具端回 initialize → 单 result、无 `responses` 键）、`test_tools_list_dedups_shared_tool_across_servers`（名字唯一且有序、`_meta` 与 UUID 最小连接一致）、`test_tool_call_routes_to_owning_server`（独占工具打到唯一持有者；同名工具打到 `tools/list` 展示的那台）
   - `tests/test_server.py` 新增 5 项（`604a6881`/`fd1d22d`）：`test_health_accepts_bearer_header`、`test_health_accepts_raw_key_header`、`test_health_rejects_wrong_header_key`、`test_header_takes_precedence_over_query`、`test_health_still_accepts_query_key_for_compat`；`test_stats_still_accepts_query_key_for_compat` 为 stats 侧兼容用例（断言 `mcp_server_connections` 存在）
   - `tests/test_utils.py` 新增 2 项（`7077bab`）：`test_load_is_lazy_and_creates_file_on_first_read`（构造 `ConfigManager` 不落盘、首次取值才创建配置）、`test_get_local_ip_is_cached`（同一进程内结果一致）
   - `tests/test_handlers.py` 新增 4 项（`8718b78`）：`test_server_request_is_forwarded_to_robot`、`test_server_notification_is_forwarded_to_robot`、`test_response_with_unknown_id_is_not_forwarded`、`test_server_message_is_dropped_when_no_robot`
   - `tests/test_utils.py` 新增 `ConfigPeekTests`（2 项，`8718b78`）：`test_peek_does_not_create_config_file`、`test_peek_falls_back_to_default_without_any_file`
   - `mcp-bridge/tests/test_mcp_bridge.py` 新增 `WirePayloadTests`（4 项，`8718b78`/`d846699`）：null `result` 保留且 null `id` 丢弃（替身对象）、通知不出现 `id`/`error`、错误响应保留 `error`、`result` 为 dict 时原样保留

7. `src/utils/util.py`（L4）
   - `get_local_ip() -> str` 结果缓存（`_cached_local_ip`），探测逻辑拆到 `_probe_local_ip() -> Optional[str]`：先 `gethostbyname(gethostname())`，非 `127.x` 即返回；否则 UDP `connect(("192.0.2.1", 9))`（RFC 5737 TEST-NET-1，0.2s 超时）取出口地址；均失败回退 `127.0.0.1`
8. `src/utils/logger.py`（L3）
   - `InterceptHandler.emit` 删除 `sys._getframe(6)`，改为 `frame, depth = logging.currentframe(), 2` + `while frame is not None and frame.f_code.co_filename in (logging.__file__, __file__)`，深度动态计算，不再依赖 Python/日志实现
9. `src/utils/config.py`（L5）
   - `__init__` 只记路径与 `_loaded=False`；新增 `_ensure_loaded()`，`get/getint/getboolean` 首行调用，`reload()` 强制重载（`_loaded=True` 后 `_load_config()`）
   - `_check_and_generate_key` 的 `print` 改为 `self.generated_key = True`，密钥值仍不落日志
   - 边界：仅 `src.utils.config` 自身导入不再落盘；`src.utils.logger` 导入即 `LoggerManager()` 读配置（`logger.py:141` → `:64/:73/:84/:88`），应用入口的首次配置加载位置未变（N4）
10. `src/utils/jsonrpc.py` + `src/server.py`（L2）
   - 删除无调用方的 `create_authentication_error`（仓库内零引用）
   - `server.py` 新增 `_authentication_error_response() -> dict`（第 175 行），`health_check`/`stats` 的鉴权失败分支收敛为该函数，错误码 `-32004` 与 `error.data.details` 完全不变
   - lifespan 增加：`if config.generated_key: logger.warning("已自动生成新的 32 位 server.key ...")`
11. `src/handlers/websocket_handler.py`（L7）
   - `_handle_tools_list_response`、`_handle_initialize_response`、`_handle_tool_call_request` 的错误回包失败分支由 `logger.error(f"...{e}")` 改 `logger.exception(...)`；`server.py` WS 收发循环的两处同类改动并注明“记录堆栈后结束该连接，由 finally 注销”

12. `src/handlers/websocket_handler.py`（N1）
    - 新增 `_is_server_initiated_message(message_data) -> bool`：有 `method` 且（无 `id` 或 `id` 不在 `connection_manager.pending_responses`）即为服务器主动消息；`_handle_mcp_server_message` 末尾调用 `_forward_to_robot_agents(agent_id, mcp_connection_uuid, message_data)`
    - 新增 `async _forward_to_robot_agents(...)`：遍历 `connection_manager.get_robot_connections_by_agent(agent_id)`，逐个小智端 `forward_to_robot_by_uuid(robot_conn.connection_uuid, message_data)`，原样转发不改字段
    - 删除空的 `WebSocketHandler.__init__`
13. `mcp-bridge/mcp_bridge.py`（N2/N3）
    - 新增 `_dump_message(message: Any) -> str`：`data = json.loads(message.model_dump_json(by_alias=True))`，`for key in ("id", "error"): if data.get(key) is None: data.pop(key, None)`，再 `json.dumps(data, ensure_ascii=False)`；`relay_remote_to_ws` 改用它
    - `run_once` 中 `done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)`
14. `src/utils/config.py` + `src/utils/logger.py` + `src/server.py`（N4）
    - `ConfigManager._peek_parser()` 只读解析 `config_file` 或根目录 `mcp-endpoint-server.cfg`（都不存在返回 None）；`peek/peekint/peekboolean` 不创建目录/文件、不生成 `server.key`
    - `LoggerManager._setup_logger` 的 4 处 `config.get/getint` 改 `peek/peekint`；`server.py` CORS 判断改 `peekboolean/peek`

验证：`python -m py_compile` 全量通过；本地以 loguru stub 跑通 `test_core` 21 项 + `test_handlers`/`test_forwarding_lock` 7 项 = 28 项全绿（本机缺 fastapi，`test_server`/`test_integration` 无法本地执行）；header 鉴权逻辑另用抽出的 helper 做了 9 组无依赖自检（Bearer / 裸密钥 / 错误密钥 / 头优先 / query 兼容 / 空值）全部符合预期；`7077bab` 另做无依赖行为自检：延迟加载（构造不落盘 → 首次取值建文件 → `reload` 保留 key）、空 key 触发自动生成（`generated_key=True`）、`get_local_ip()` 两次调用一致（本机返回 `192.168.50.245`）；远端 `core tests`（py3.11/3.12 矩阵 + coverage 75% 门槛，全套 56 项、覆盖率 80%，含 3 项双工具端 e2e）与 `docker publish` 在 `4dca5650`、`e36ef14`、`fd1d22d`、`d6ae8e5`、`7077bab` 上均通过。`8718b78` 本地：`test_core` 21 项 + `test_handlers`/`test_forwarding_lock` 11 项 = 32 项全绿、`WirePayloadTests` 4 项通过；远端 `core tests`（56 项，coverage ≥75%）、`mcp-bridge tests`（16 项）、`docker publish` 通过，`d846699` 的 `mcp-bridge tests` 与 `docker publish` 亦通过（见开头六张 CI 表）。

未触碰：workflow 触发条件、依赖版本、JSON-RPC 字段语义（S1 修复本身除外）、工具名（不加服务器前缀）。

### 遗留事项（按 `2aed70f` 最终状态）

问题清单 S1、M1–M8、L1–L8、复查 N1–N4 与 bridge 审查 B1–B12 已全部落地，无未实施项。以下为范围外/后续可选事项，仅登记不改代码：

| # | 事项 | 说明 / 建议 |
|---|---|---|
| 1 | query `key` 兼容分支移除 | 兼容窗口到期后删除 `server.py` 的 `key` 参数与 `_query_key_warning_emitted` |
| 2 | `connection_manager` 拆分 | 单类仍约 560 行、五种职责；建议按“连接表 / pending 聚合 / 工具索引 / 统计”拆分，属重构 |
| 3 | 限流与背压 | 当前无请求级限流，待确认是否由前置网关承担 |
| 4 | 外部导入兼容 | `jsonrpc.create_authentication_error` 已删除；若仓库外脚本 import 该函数会 ImportError（仓库内零引用已确认） |
| 5 | 日志 `{name}` 字段重测 | L3 改了调用者定位逻辑，建议观察一次真实运行日志的模块名是否仍准确 |
| 6 | bridge base 镜像 digest | B8 已加 `HEALTHCHECK` 与 `healthcheck.py`；digest 因构建机无法访问 registry 未固定，发布前在 `mcp-bridge/Dockerfile` 补 `@sha256:...` |
| 7 | stdio 子进程环境白名单 | B5 只做了“传入映射即隔离”，子进程仍继承 bridge 进程全量环境；白名单化（`PATH/HOME/LANG/SystemRoot`）有兼容风险，按需再做 |

## 六、结论与优先顺序

整体结论（截至 `2aed70f`）：
- **架构：中等偏上。** 分层清楚、单进程模型简单、ID 变换与聚合有设计；`connection_manager` 仍过重（约 560 行、五种职责）、全局单例耦合仍在。多服务器聚合语义（S1）、同名工具 list/call 一致性（M4）与客户端 `tools/list` 聚合去重（M8）均已闭环，整体从“单服务器能跑、多服务器不可靠”提升为多服务器可用；剩余短板是彻底消除同名歧义仍需命名空间或显式路由表（当前用 `_meta` 暴露被遮蔽连接而非改名）。
- **功能完整性：单/多服务器均已闭环**（认证/工具列表/工具调用/超时/统计均有实现与测试）；多服务器 initialize/ping（S1）、同名工具去重与路由一致（M4/M8）、优雅停机（M6）均已闭环，双工具端 e2e（`DualToolServerRelayTests` 3 项）在 CI 双矩阵真实 WS 链路通过。
- **代码质量：良好。** 命名统一、注释充分、错误码集中、核心路径有测试；已修重复模板（M7）、死状态（L1）、类型注解错误（M2）、health/stats 重复校验与 key 从 URL 迁到 `Authorization` 头（M3 全部完成）；死代码、日志栈深、外网探测、导入副作用与宽泛异常（L2–L5、L7）均已收口；bridge 侧 12 项（停止信号、入站 `result:null`、`params:null`、args 脱敏、Windows 反斜杠、`HEALTHCHECK`、测试替身与退避用例）亦全部收口，问题清单无遗留项。

优先处理顺序（按 `2aed70f` 最终状态）：
1. **P0 — 已完成（S1）**：多服务器 initialize/ping 已修并有单测。
2. **P1 — 已完成（M1 / M2 / M4 / M5 / M8）**：通知不回包、类型注解、同名工具去重与 list/call 一致、重复注册取消旧任务、客户端 `tools/list` 聚合去重。
3. **P2 — 已完成（M3 / M6 / M7）**：M3 去重与 key header 化均已落地，query 兼容保留一个发布周期，下个周期移除。
4. **P3 — 已完成（L1–L8 全部）**：L2 死代码、L3 栈深、L4 探测、L5 导入副作用、L7 异常日志均已落地。
5. **P4 — 已完成（B1–B12）**：B2/B4/B7 `cc144e00`、B1 `40a4f56`、B3 `55f2b19`、B5/B6/B9/B10/B11/B12 `2aed70f`；B8 除 base digest（需 registry 访问）外完成，见第八节。
6. **待办（0 项）**：问题清单 17 项、复查 N1–N4、bridge B1–B12 均已落地；仅登记两项范围外可选事项（query `key` 兼容分支到期移除、`connection_manager` 拆分重构），见第五节遗留表。

（待补充：真实多服务器部署下 initialize/ping 的抓包或日志，用于确认 S1 修复在生产链路上的效果；L2–L5 的取舍已按默认安全选项落地（延迟加载、缓存探测、删除零引用死函数），部署侧如依赖旧行为需回看提交 `7077bab`。兼容窗口到期后需移除 query `key` 分支与 `_query_key_warning_emitted`。双工具端 e2e 已覆盖“注册顺序与 UUID 序不一致”的路由一致性，但未覆盖工具端中途掉线重连后的重新注册时序。）

## 七、复查（2026-11-22，基线 `7077bab`）

逐条回读 `7077bab` 快照：S1、M1–M8、L1–L8 共 17 项全部在代码中确认到位（死代码/死字段零命中：`connection_timestamps`、`create_authentication_error`、`_getframe`、`8.8.8.8`）。复查另发现 4 项此前清单未覆盖的问题，其中 N1–N4 已于 `8718b78` 全部修复（下表状态列），bridge 用例问题由 `d846699` 修正：

| 编号 | 位置 | 问题 | 影响 | 建议 | 状态 |
|---|---|---|---|---|---|
| N1 | `src/handlers/websocket_handler.py:40-115` | MCP 服务器主动发起的消息被静默丢弃：只处理 `tools/list`、`initialize` 响应与命中 `pending_responses` 的响应；服务器发来的 `method` 请求（`sampling/createMessage`、`roots/list`、`elicitation/create`）与通知（`notifications/tools/list_changed`、`notifications/message`）无匹配 pending，直接消失 | 服务器侧工具变更通知到不了智能体端，工具列表不会热更新；采样/roots 等反向请求永远无响应。静态工具集场景下无感 | 在 `_handle_mcp_server_message` 增加分支：无 `id` 的通知、带 `id` 但不在 pending 的 `method` 消息，转发给该 agent 的小智端连接 | ✅ 已修（`8718b78`）：新增 `_is_server_initiated_message` + `_forward_to_robot_agents`，命中后原样转发给该 agent 的全部小智端连接，无小智端时仅 debug 日志；补 4 项 handler 单测（主动请求 / 通知 / 未知 id 响应不转发 / 无小智端） |
| N2 | `mcp-bridge/mcp_bridge.py:324-336`（原 `:329`） | `model_dump_json(by_alias=True, exclude_none=True)` 会删除值为 `null` 的字段 | 上游若返回 `{"jsonrpc":"2.0","id":1,"result":null}`，转发后变成既无 `result` 也无 `error` 的响应，严格客户端判协议错误 | 只省略 `id`/`error` 的 null（保留 `result`） | ✅ 已修（`8718b78`）：新增 `_dump_message`（`mcp_bridge.py:324-336`），只剔除 null 的 `id`/`error`。**注**：当前固定的 MCP SDK 把 `JSONRPCResponse.result` 声明为 dict，`result:null` 在 `JSONRPCMessage` 校验阶段即被拒，故该策略用替身对象覆盖（`d846699`） |
| N3 | `mcp-bridge/mcp_bridge.py:397`（原 `:380`） | `done, pending = await asyncio.wait(...)` 的 `pending` 从未使用（`finally` 已统一 cancel） | 仅冗余，无功能影响 | 改 `done, _ = await asyncio.wait(...)` | ✅ 已修（`8718b78`）：`mcp_bridge.py:397` 改为 `done, _ = ...` |
| N4 | `src/utils/logger.py:141` → `:64/:73/:84/:88` | `logger_manager = LoggerManager()` 在导入时即读配置，故 `import src.server` 仍会在导入阶段加载/创建 `data/*.cfg` | 上一轮 L5 的“import 不再产生磁盘副作用”只对单独导入 `src.utils.config` 成立，应用入口行为未变 | 只读 peek 或惰性初始化 | ✅ 已修（`8718b78`）：`ConfigManager` 新增只读 `peek`/`peekint`/`peekboolean`（`config.py:112-160`），`logger._setup_logger` 与 `server.py` CORS 判断改用它们；实测导入 `src.utils.config` + `src.utils.logger` 后 `data/` 不存在、`_loaded` 仍为 False，首次加载推迟到 lifespan/首个请求。残余：日志目录/文件仍按既有行为在日志初始化时创建 |

另一处已顺手清理：`WebSocketHandler.__init__`（原 `websocket_handler.py:21-22`，只有 `pass`）已删除（`8718b78`）。

结论：**问题清单本身无遗漏**（17 项全部落地）；复查新发现 N1–N4 已于 `8718b78` 全部修复——N1 打通了服务器主动消息（工具列表热更新、sampling/roots 反向请求）链路，N2 保留 null `result`，N3 去掉冗余变量，N4 用只读 peek 消除导入期配置落盘。`mcp-bridge tests` 首跑因用例构造了 SDK 不允许的 `result:null` 失败，`d846699` 改用替身对象后通过。

## 八、mcp-bridge 其余模块审查（2026-11-22，基线 `d846699`，收尾 `2aed70f`）

范围：`mcp-bridge/mcp_bridge.py`（全量复审，N2/N3 修复后 500 行）+ `tests/fake_stdio_server.py`、`tests/fake_fastmcp_server.py`、`Dockerfile`、`.env.example`、`docker-compose.example.yml`、`requirements.txt`、`README.md`、`.dockerignore`。

| 编号 | 严重度 | 位置 | 问题 | 影响 | 修改/建议 | 状态 |
|---|---|---|---|---|---|---|
| B1 | 中等 | 修复后行号：`mcp_bridge.py:406-430`（`run_once`）、`:439`（`run_forever`）；原 `448-451`（`install_signal_handlers`） | 停止信号不打断活跃连接：`install_signal_handlers` 只 `stop_event.set()`，而 `run_forever` 阻塞在 `run_once` 的 `async for` / `asyncio.wait` 里，`stop_event` 要等当前生命周期结束才被检查 | `docker stop`/SIGTERM 后进程不退出，直到连接自然断开或容器 10s 宽限期被 SIGKILL，进行中的 `tools/call` 被截断 | `run_once` 以 task 运行，加 `stop_task = asyncio.create_task(stop_event.wait())`，`await asyncio.wait({conn_task, stop_task}, return_when=FIRST_COMPLETED)`；stop 命中即 cancel `conn_task`、关闭 WS 与子进程，再返回 | ✅ 已修（`40a4f56`）：`run_once(cfg, stop_event=None)` 把 `stop_event.wait()` 作为 `stop_task` 与两个 relay task 一起 `asyncio.wait(..., FIRST_COMPLETED)` 竞速，命中即取消 relay、由 `async with` 正常关闭 WS 与 transport；`run_forever` 传入 `stop_event` 且已停止时不再打印 "one side closed"；补 `StopTests` 2 项（`run_once` 2s 内返回、`run_forever` 只跑一轮即退出）。进程内实测 `run_forever` 停止后 **0.00s** 返回（修复前会一直挂到连接断开）；CI `mcp-bridge tests` / `docker publish` 均 ✅ |
| B2 | 中等 | `mcp_bridge.py:324-336`（`_dump_message`），经 N1 转发路径 `src/handlers/websocket_handler.py:101-115` | 出站只剔 null 的 `id`/`error`，未剔 null 的 `params`。实测 `JSONRPCRequest(id=5, method="roots/list")` 序列化为 `{"method":"roots/list","params":null,"jsonrpc":"2.0","id":5}`；JSON-RPC 2.0 要求 `params` 若存在必须是结构化对象/数组 | N1 新打通的服务器主动请求多为无参数（`roots/list`、`ping`），转发给小智端时带 `params:null`，严格实现的客户端会判非法 → 反向请求仍得不到响应，刚修的功能在最常见分支上失效 | `for key in ("id", "error", "params"): if data.get(key) is None: data.pop(key, None)`，并补一条 `roots/list` 序列化用例 | ✅ 已修（`140d279`）：`for key in ("id", "error", "params")`，文档同步说明；补 `test_params_null_is_dropped_for_argumentless_request`、`test_params_object_is_kept_intact` |
| B3 | 中等 | 修复后：`mcp_bridge.py:356-388`（新增 `_RawJSONRPCMessage`、`_is_null_result_response`）、`:404-418`（`relay_ws_to_remote` 分支） | 入站校验用 `JSONRPCMessage.model_validate`。实测该 SDK（1.30.0）把 `JSONRPCResponse.result` 声明为 `dict`，`{"jsonrpc":"2.0","id":1,"result":null}` 直接抛 4 条 ValidationError，被当作非法消息 `continue` 丢弃（仅 warning） | 小智端/上游返回合法但 `result:null` 的响应会被静默吞掉，MCP Server 侧只能等到超时；与 N2 修好的出站方向不对称 | 校验失败时若结构形似 JSON-RPC 响应（有 `id` 且含 `result`/`error`），按原始文本透传（构造 `SessionMessage` 时用 `model_construct` 绕过类型校验），或对 `result is None` 改写为 `result:{}` 前先记录 warning；至少把日志提到 warning 并带 `id` | ✅ 已修（`55f2b19`）：采用「原样透传」策略——`_is_null_result_response` 判定（jsonrpc=2.0、无 method、有 id、无 error、result 为 null），命中则用 `_RawJSONRPCMessage` 包装，其 `model_dump`/`model_dump_json` 忽略 `exclude_none`，保证三种 transport 序列化时不丢 `result:null`，且不改写业务数据；其余非法消息仍 warning 后丢弃。补 4 项用例 |
| B4 | 中等 | `mcp_bridge.py:453-459`（`_public_config`） | `--print-config` 只脱敏 `endpoint` 与 `headers`，`args` 原样打印；实际部署常把 token 写进 `MCP_ARGS`（如 `["server.py","--token","xxx"]`） | README 承诺“`--print-config` 也会隐藏 endpoint 的 token 和 headers 值”，但 args 里的密钥仍会出现在终端/工单日志里 | 对 `args` 中形如 `--token/--key/--secret/--password` 的下一个元素掩码为 `***`，或整体输出 `args` 长度与前两项 | ✅ 已修（`cc144e00`）：新增 `_redact_args`（覆盖 `--token xxx` 与 `--token=xxx`），`_public_config` 输出前掩码；补 `test_print_config_redacts_secret_args`，README 同步 |
| B5 | 轻微 | `mcp_bridge.py:128-131` | stdio 子进程环境取 `dict(os.environ)`，且 `from_env(env=自定义映射)` 时仍用真实 `os.environ` | ①全量宿主环境（含各类 token）注入 MCP 子进程，扩大泄露面；②用自定义 env 的测试无法隔离 | `base = dict(os.environ if env is None else env)`；再考虑白名单（`PATH/HOME/LANG/SystemRoot`）+ `MCP_ENV` 覆盖 | ✅ 已修（`2aed70f`）：显式传入映射时子进程环境取该映射副本（`dict(env)`），不再混入宿主 `os.environ`；补 `EnvIsolationTests` 1 项断言“环境键集合 = 传入映射 + `MCP_ENV`”。白名单仍建议但属兼容风险，未做 |
| B6 | 轻微 | `mcp_bridge.py:212-224` | `shlex.split` 走 POSIX 语义，Windows 下反斜杠被当转义符：`MCP_ARGS='C:\Program Files\x.py'` 解析成 `C:Program Filesx.py` | Windows 宿主机上 stdio 模式偶发找不到脚本，且报错指向子进程启动失败，难定位 | `shlex.split(raw, posix=(os.name != "nt"))`，或文档明确要求用 JSON 数组（推荐后者更稳） | ✅ 已修（`2aed70f`）：新增 `_split_shell_args`，Windows 下 `shlex.split(posix=False)` 后剥一层引号，`C:\\tools\\server.py` 与带空格的引号路径均保持完整；补 `ShellArgsTests` 3 项；README 注明 JSON 数组为推荐写法 |
| B7 | 轻微 | `mcp_bridge.py:216-224` | `shlex.split` 抛 `ValueError: No closing quotation` 未包装，`main` 只捕 `ConfigError` | 引号写错时输出 Python traceback 而非 `config error: ...`，退出码 1 而非 2 | `try: return shlex.split(raw) except ValueError as exc: raise ConfigError(...) from exc` | ✅ 已修（`cc144e00`）：`shlex.split` 的 `ValueError` 包装为 `ConfigError`；补 `test_unclosed_quote_in_args_is_a_config_error`，实测输出 `config error: MCP_ARGS must be a JSON array or a shell-like string`、退出码 2 |
| B8 | 轻微 | `Dockerfile:1-14` | 无 `HEALTHCHECK`、base 镜像未固定 digest；以 root 装依赖后切 `bridge` 用户（正确），但 `/app` 属 root | 容器僵死无法被编排系统感知；重装时 base 漂移导致不可复现 | 加 `HEALTHCHECK`（如 `python -c "import socket..."` 探 endpoint 端口不现实，可用进程存活探针）或明确不提供；base 固定到 `python:3.12-slim@sha256:...` | ✅ 基本完成（`2aed70f`）：新增 `mcp-bridge/healthcheck.py`（扫 `/proc/*/cmdline` 是否仍有 `mcp_bridge.py` 进程）并声明 `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3`，补 3 项单测；Dockerfile 已注释“发布前锁 digest”。**digest 未落盘**：构建机无法访问 registry（`curl auth.docker.io` 返回 000），保留 `python:3.12-slim` 标签 |
| B9 | 轻微 | `tests/fake_stdio_server.py:35` | `params = request.get("params") or {}`，若 `params` 是数组，后续 `.get` 抛 `AttributeError` 使假服务崩溃 | 测试替身健壮性问题，可能掩盖被测代码的错误路径 | `params = request.get("params")`，用时判 `isinstance(params, dict)` | ✅ 已修（`2aed70f`）：`params` 与 `params.arguments` 非 dict 时回退 `{}` |
| B10 | 轻微 | `tests/fake_fastmcp_server.py:12-13` | `sys.argv[1]`、`int(sys.argv[2])` 无校验 | 参数缺失时 `IndexError`，测试报错信息不直观 | 补默认值/用法提示 | ✅ 已修（`2aed70f`）：`main(argv)` 校验参数个数与端口可解析，缺参/非法端口打印用法并返回 2；补 `TestDoubleGuardTests` 1 项 |
| B11 | 轻微 | `README.md:84-105`、`requirements.txt:1` | 文档未提示 `MCP_ENV` 会把全量宿主环境注入子进程、`MCP_ARGS` 反斜杠需用 JSON 数组；依赖下限 `mcp>=1.30.0,<2` 过宽（B3 正是不同 SDK 校验行为的产物） | 使用者按文档配置会在 Windows/代理环境踩坑；升级 SDK 可能再次改变校验行为 | README 增两条注意事项；依赖改 `mcp~=1.30.0` 或写明“已在 1.30.0 验证” | ✅ 已修（`2aed70f`）：`requirements.txt` 改 `mcp>=1.30.0,<1.31`；README 配置表补 `MCP_ENV` 继承全量.env 说明、`MCP_ARGS` Windows 反斜杠限制，注意事项新增两条 |
| B12 | 轻微 | `tests/test_mcp_bridge.py`（收尾前 26 项） | 测试缺口：`params:null`、`--print-config` 脱敏、引号错误、停止信号、入站 `result:null`/非法消息已随 B2/B4/B7/B1/B3 补齐；仅剩 重连退避用例（`MCP_RECONNECT_INITIAL/MAX` 的指数退避无断言） | 退避逻辑若被改坏，测试不会发现 | 补一条退避断言（monkeypatch `asyncio.sleep`/`wait_for` 记录间隔） | ✅ 已修（`2aed70f`）：新增 `ReconnectBackoffTests`，断言退避序列 `0.1 → 0.2 → 0.25`（`reconnect_max` 封顶）；全套用例 26 → 35 项 |

落地记录：
- **B2** `140d2797`（2 files）：`mcp-bridge tests` run 45006954669 ✅、`docker publish` run 45006954674 ✅；本地 `WirePayloadTests` 6 项 + `ConfigTests` 7 项 = 13 项通过；实测 `roots/list` 序列化后不含 `params`。
- **B1** `40a4f56a7697880c6b2af151fd7055a0f32840cc`（2 files：`mcp-bridge/mcp_bridge.py`、`mcp-bridge/tests/test_mcp_bridge.py`）：`mcp-bridge tests` run 45009512285 ✅、`docker publish` run 45009512312 ✅；本地 `StopTests`+`ConfigTests`+`WirePayloadTests` 共 17 项通过；in-process 实测停止后 0.00s 返回。
- **B3** `55f2b195372e1fad17fc4c50b0aa7d8d14b103d8`（2 files：`mcp-bridge/mcp_bridge.py`、`mcp-bridge/tests/test_mcp_bridge.py`）：`mcp-bridge tests` run 45009751856 ✅、`docker publish` run 45009751828 ✅；本地 `InboundRelayTests`+`WirePayloadTests`+`StopTests`+`ConfigTests` 共 21 项通过。
- **B5/B6/B8/B9/B10/B11/B12** `2aed70fe60ae9f089320d844ccd30637ccd45825`（8 files：`mcp_bridge.py`、新增 `healthcheck.py`、`Dockerfile`、`README.md`、`requirements.txt`、`tests/test_mcp_bridge.py`、`tests/fake_stdio_server.py`、`tests/fake_fastmcp_server.py`）：`mcp-bridge tests` run 45010332564 ✅、`docker publish` run 45010332608 ✅（镜像构建通过，即 `HEALTHCHECK` 语法有效）；本地定向 30 项 OK、全套 35 项中仅 3 项 e2e 因沙箱禁止子进程 `CreateFile` 报 `PermissionError`（环境限制）；`py_compile` 全过。B8 的 base digest 因构建机无 registry 访问未固定（已在 Dockerfile 注释说明）
- **B7 + B4** `cc144e008a2b6fbf081723749857e563ba87f8bc`（3 files：`mcp-bridge/mcp_bridge.py`、`mcp-bridge/tests/test_mcp_bridge.py`、`mcp-bridge/README.md`）：`mcp-bridge tests` run 45007976904 ✅、`docker publish` run 45007976865 ✅；本地 15 项通过；CLI 实测 `--print-config` 输出 `--token ***`、`--key=***`，引号不闭合输出 `config error: ...` 且退出码 2。

结论（第八节）：
- **无阻断级缺陷**。`mcp_bridge.py` 的转发主链路（`relay_*`、`run_once` 生命周期、`run_forever` 退避、配置解析与脱敏、三种 transport 构造）结构清晰，错误处理与日志尺度合理；测试替身与打包文件也可用。
- 曾真正影响功能的是 **B2（已修）**：N1 打通的“服务器主动消息”方向，最常见的无参数请求（`roots/list`、`ping`）会带 `params:null` 出去被严格客户端拒收——即修复前 N1 只对带参请求（如 `sampling/createMessage`）真正生效；`140d279` 剔除 null `params` 后该分支闭环。
- **B1**（优雅停止）`40a4f56`、**B3**（入站 `result:null` 原样透传）`55f2b19`、**B4**/**B7** `cc144e00`、**B5/B6/B9/B10/B11/B12** `2aed70f` 均已修复——**第八节 12 项全部落地**（B8 仅 base digest 因无 registry 访问未固定）。
- 建议顺序：**第八节 12 项全部落地**——中等项 `140d279`/`cc144e00`/`40a4f56`/`55f2b19`；轻微项 `2aed70f`（B5/B6/B9/B10/B11/B12 全修，B8 除 base digest 外完成）。遗留：`mcp_bridge.py` 的 stdio 子进程仍继承 bridge 进程全量环境（白名单化有兼容风险，按需再做）；`docker-compose.example.yml` 未加 digest 锁。

