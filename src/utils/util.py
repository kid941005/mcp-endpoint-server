"""网络工具。"""

import socket
from typing import Optional

# 缓存出口地址：探测有系统调用开销，且启动后不会变化。
_cached_local_ip: Optional[str] = None

# 仅用于确定出口网卡的路由探测目标：TEST-NET-1（RFC 5737），UDP connect
# 不产生实际流量，也不依赖公网可达性。
_ROUTE_PROBE_TARGET = ("192.0.2.1", 9)


def _probe_local_ip() -> Optional[str]:
    """尝试获取本机出口地址，失败返回 None。"""
    try:
        resolved = socket.gethostbyname(socket.gethostname())
    except OSError:
        resolved = None
    if resolved and not resolved.startswith("127."):
        return resolved

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.2)
        sock.connect(_ROUTE_PROBE_TARGET)
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def get_local_ip() -> str:
    """获取本机出口地址，失败时回退到 loopback；结果缓存，避免重复探测。"""
    global _cached_local_ip
    if _cached_local_ip is None:
        _cached_local_ip = _probe_local_ip() or "127.0.0.1"
    return _cached_local_ip
