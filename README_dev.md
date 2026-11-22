# MCP Endpoint Server

## 开发说明

### 项目结构
```
mcp-endpoint-server/
├── src/
│   ├── core/                 # 核心模块
│   │   └── connection_manager.py
│   ├── handlers/             # 处理器
│   │   └── websocket_handler.py
│   ├── utils/                # 工具模块
│   │   ├── __init__.py       # 版本号
│   │   ├── aes_utils.py      # AES 加解密
│   │   ├── config.py         # 配置管理
│   │   ├── jsonrpc.py        # JSON-RPC 协议
│   │   ├── logger.py         # 日志管理
│   │   └── util.py           # 网络工具
│   └── server.py             # 主服务器
├── mcp-bridge/               # stdio/SSE/HTTP 到 WebSocket 的桥
├── tests/                    # 单元与端到端测试
├── data/                     # 运行时配置目录
├── logs/                     # 日志目录
├── main.py                   # 主入口
├── requirements.txt          # 依赖文件
├── requirements-dev.txt      # 测试依赖
├── Dockerfile
└── README.md                 # 说明文档
```
## 安装依赖

```bash
conda remove -n mcp-endpoint-server --all -y
conda create -n mcp-endpoint-server python=3.12 -y
conda activate mcp-endpoint-server

pip install -r requirements.txt

python main.py
```

### 测试数据
```json
{"id":0,"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{"sampling":{},"roots":{"listChanged":false}},"clientInfo":{"name":"xz-mcp-broker","version":"0.0.1"}}}


{"jsonrpc":"2.0","method":"notifications/initialized"}


{"id":1,"jsonrpc":"2.0","method":"tools/list","params":{}}


{"id":2,"jsonrpc":"2.0","method":"ping","params":{}}


{"id":10,"jsonrpc":"2.0","method":"tools/call","params":{"name":"calculator","arguments":{"python_expression":"130000 * 130000"},"serialNumber":null}}
```

