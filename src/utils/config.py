"""
配置管理工具
"""

import os
import configparser
import secrets
from typing import Optional
from pathlib import Path


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_file: str = "data/.mcp-endpoint-server.cfg"):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        # 延迟加载：构造 ConfigManager 不读写磁盘，首次取值时才加载/创建配置，
        # 避免 import 即产生文件副作用（也便于测试注入路径）。
        self._loaded = False
        # 本次加载是否自动生成了 server.key，由调用方（启动流程）决定如何提示，
        # 避免在配置模块里直接 print。
        self.generated_key = False

    def _ensure_loaded(self):
        """首次访问时才加载配置。"""
        if self._loaded:
            return
        self._loaded = True
        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding="utf-8")
            # 检查并生成key
            self._check_and_generate_key()
        else:
            # 如果配置文件不存在，从根目录拷贝
            self._copy_config_from_root()

    def _copy_config_from_root(self):
        """从根目录拷贝配置文件到data目录"""
        root_config = "mcp-endpoint-server.cfg"
        if os.path.exists(root_config):
            # 确保data目录存在
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            # 拷贝配置文件
            import shutil

            shutil.copy2(root_config, self.config_file)

            # 重新加载配置
            self.config.read(self.config_file, encoding="utf-8")
            # 检查并生成key
            self._check_and_generate_key()
        else:
            # 如果根目录也没有配置文件，则创建默认配置
            self._create_default_config()

    def _check_and_generate_key(self):
        """检查key是否存在且长度足够，如果不足则生成新的。"""
        try:
            current_key = self.config.get("server", "key", fallback="")
            if not current_key or len(current_key) < 32:
                new_key = self._generate_random_key()
                self.config.set("server", "key", new_key)
                with open(self.config_file, "w", encoding="utf-8") as f:
                    self.config.write(f)
                # 绝不把密钥打印到 stdout/日志，避免泄露；仅记录事件供启动流程提示。
                self.generated_key = True
        except Exception as e:
            raise RuntimeError(f"检查或生成 server.key 失败: {e}") from e

    def _generate_random_key(self) -> str:
        """生成 32 字符的密码学随机密钥。"""
        return secrets.token_hex(16)

    def _create_default_config(self):
        """创建默认配置"""
        self.config["server"] = {
            "host": "127.0.0.1",
            "port": "8004",
            "debug": "false",
            "log_level": "INFO",
            "key": self._generate_random_key(),  # 生成默认密钥
        }

        self.config["websocket"] = {
            "max_connections": "1000",
            "ping_interval": "30",
            "ping_timeout": "10",
            "close_timeout": "10",
        }

        self.config["security"] = {"allowed_origins": "*", "enable_cors": "true"}

        self.config["logging"] = {
            "log_file": "logs/mcp_server.log",
            "max_file_size": "10MB",
            "backup_count": "5",
        }

        # 确保目录存在
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

        # 保存默认配置
        with open(self.config_file, "w", encoding="utf-8") as f:
            self.config.write(f)

    def get(self, section: str, key: str, default: Optional[str] = None) -> str:
        """获取配置值"""
        self._ensure_loaded()
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def getint(self, section: str, key: str, default: int = 0) -> int:
        """获取整数配置值"""
        self._ensure_loaded()
        try:
            return self.config.getint(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def getboolean(self, section: str, key: str, default: bool = False) -> bool:
        """获取布尔配置值"""
        self._ensure_loaded()
        try:
            return self.config.getboolean(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def reload(self):
        """重新加载配置（忽略延迟加载标记，强制重新读取）"""
        self._loaded = True
        self.generated_key = False
        self._load_config()


# 全局配置实例
config = ConfigManager()
