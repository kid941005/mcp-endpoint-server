"""
工具模块
"""

import platform

__version__ = "0.0.7"

# 启动日志中一并展示的第三方依赖（用发行包名，而非导入名）。
_RUNTIME_PACKAGES = ("fastapi", "starlette", "uvicorn", "pydantic", "loguru", "pycryptodome")


def get_runtime_versions() -> dict:
    """返回服务版本、Python 版本与关键依赖版本。

    版本取自已安装发行包的元数据（importlib.metadata），不依赖该依赖是否
    已被 import；包未安装或元数据缺失时记为 unknown 而不是抛异常，避免
    版本展示反过来成为启动失败的原因。
    """
    from importlib.metadata import PackageNotFoundError, version as package_version

    versions = {"version": __version__, "python": platform.python_version()}
    for name in _RUNTIME_PACKAGES:
        try:
            versions[name] = package_version(name)
        except PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def format_runtime_versions(versions: dict | None = None) -> str:
    """把版本字典拼成单行日志文本，便于启动日志与排查时一眼比对。"""
    data = get_runtime_versions() if versions is None else versions
    return (
        "Python {python} | FastAPI {fastapi} | Uvicorn {uvicorn} | "
        "Pydantic {pydantic} | Loguru {loguru} | PyCryptodome {pycryptodome}"
    ).format(**data)
