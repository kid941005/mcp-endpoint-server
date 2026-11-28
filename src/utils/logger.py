"""
日志管理工具
"""

import math
import os
import sys
import logging
from loguru import logger
from .config import config

# 版本号
from . import __version__ as VERSION


class InterceptHandler(logging.Handler):
    """拦截标准库日志并转发到loguru"""

    def emit(self, record):
        # 获取对应的loguru级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 从当前帧向上跳过 logging 模块与本文件的帧，定位真实调用者。
        # 不写死栈深度：Python 版本或日志实现变化时不会错位，也不会越界。
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename in (
            logging.__file__,
            __file__,
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class LoggerManager:
    """日志管理器"""

    def __init__(self):
        self._setup_logger()

    def _setup_logger(self):
        """设置日志器

        注意：本方法在模块导入时执行（全局 LoggerManager 实例化）。为保证
        import 阶段不产生磁盘副作用（不创建 data/*.cfg、不生成 server.key），
        这里只用 ConfigManager.peek* 只读读取现有配置；真正加载配置推迟到
        lifespan / 首个请求。
        """
        # 移除默认的处理器
        logger.remove()

        # 自定义格式：时间[版本号][模块路径]-级别-消息
        # 不同部分使用不同颜色
        custom_format = (
            f"<green>{{time:YYMMDD HH:mm:ss}}</green>"
            f"<blue>[{VERSION}][{{name}}]</blue>"
            "<level>-{level}-</level>"
            "<green>{message}</green>"
        )

        # 控制台处理器
        logger.add(
            sys.stdout,
            format=custom_format,
            level=config.peek("server", "log_level", "INFO"),
            colorize=True,
            backtrace=True,
            diagnose=True,
            enqueue=True,
            catch=True,
        )

        # 文件处理器（不带颜色）
        log_file = config.peek("logging", "log_file", "logs/mcp_server.log")
        if log_file:
            # 确保日志目录存在；纯文件名时 dirname 为空，不能直接 makedirs。
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            # 文件格式（不带颜色）
            file_format = f"{{time:YYYY-MM-DD HH:mm:ss}} [{VERSION}][{{name}}] {{level}} - {{message}}"

            # 获取文件大小限制
            max_file_size = config.peek("logging", "max_file_size", "10MB")
            try:
                max_bytes = self._parse_size(max_file_size)
            except (ValueError, OverflowError, TypeError):
                # 配置写错不应在 import 期崩溃，回退默认 10MB 并提示。
                print(
                    f"[logger] 无效的 logging.max_file_size={max_file_size!r}，回退 10MB",
                    file=sys.stderr,
                )
                max_bytes = 10 * 1024 * 1024

            # 获取备份数量；<=0 会让 loguru 在每次轮转后清空历史文件。
            backup_count = config.peekint("logging", "backup_count", 5)
            if backup_count <= 0:
                print(
                    f"[logger] 无效的 logging.backup_count={backup_count!r}，回退 5",
                    file=sys.stderr,
                )
                backup_count = 5

            logger.add(
                log_file,
                format=file_format,
                level=config.peek("server", "log_level", "INFO"),
                rotation=max_bytes,
                retention=backup_count,
                compression="zip",
                encoding="utf-8",
                enqueue=True,
                catch=True,
            )

    def _parse_size(self, size_str: str) -> int:
        """解析文件大小字符串（B/KB/MB/GB，大小写不敏感）。

        只接受有限正数：inf/nan/0/负数会让 loguru 的 rotation 行为异常
        （例如每条日志都轮转并压缩），统一视为无效配置。
        """
        text = str(size_str).strip().upper()
        factor = 1
        number = text
        for suffix, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
            if text.endswith(suffix):
                factor = size
                number = text[: -len(suffix)].strip()
                break
        try:
            value = float(number)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"无效的文件大小配置: {size_str!r}") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"无效的文件大小配置: {size_str!r}")
        return int(value * factor)

    def get_logger(self):
        """获取日志器"""
        return logger

    def reload(self):
        """重新加载日志配置"""
        self._setup_logger()

    def setup_uvicorn_logging(self):
        """设置uvicorn日志拦截"""
        # 拦截标准库日志
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        # 直接禁用uvicorn.access日志
        logging.getLogger("uvicorn.access").disabled = True
        logging.getLogger("uvicorn.access").propagate = False

        # 拦截其他uvicorn日志
        for name in logging.root.manager.loggerDict.keys():
            if not name.startswith("uvicorn.access"):
                logging.getLogger(name).handlers = []
                logging.getLogger(name).propagate = True


# 全局日志管理器实例
logger_manager = LoggerManager()


def get_logger(name: str = "mcp_server"):
    """获取日志器"""
    return logger.bind(name=name)
