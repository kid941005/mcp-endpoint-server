"""Utility tests for JSON-RPC, AES, config, and network helpers."""
import os
import tempfile
import unittest

from src.utils.aes_utils import decrypt, encrypt, pad_key
from src.utils.config import ConfigManager
from src.utils.jsonrpc import JSONRPCProtocol
from src.utils.util import get_local_ip


class JSONRPCTests(unittest.TestCase):
    def test_parse_request_valid(self):
        request = JSONRPCProtocol.parse_request(
            '{"jsonrpc":"2.0","id":0,"method":"tools/list","params":{}}'
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.id, 0)

    def test_parse_request_rejects_missing_method(self):
        self.assertIsNone(JSONRPCProtocol.parse_request('{"jsonrpc":"2.0","id":1}'))

    def test_parse_response_rejects_result_and_error(self):
        raw = '{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":1,"message":"x"}}'
        self.assertIsNone(JSONRPCProtocol.parse_response(raw))

    def test_notification_omits_id_and_params(self):
        data = JSONRPCProtocol.to_dict(JSONRPCProtocol.create_notification("notify"))
        self.assertNotIn("id", data)
        self.assertNotIn("params", data)


class AESTests(unittest.TestCase):
    def test_roundtrip(self):
        key = "0123456789abcdef0123456789abcdef"
        plain = '{"agentId":"agent-1"}'
        self.assertEqual(decrypt(key, encrypt(key, plain)), plain)

    def test_invalid_ciphertext_returns_none(self):
        self.assertIsNone(decrypt("0123456789abcdef", "not-base64"))

    def test_pad_key_accepts_standard_lengths(self):
        self.assertEqual(len(pad_key("a" * 16)), 16)
        self.assertEqual(len(pad_key("a" * 24)), 24)
        self.assertEqual(len(pad_key("a" * 32)), 32)
        self.assertEqual(len(pad_key("short")), 32)


class ConfigTests(unittest.TestCase):
    def test_generated_key_is_32_hex_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "server.cfg")
            manager = ConfigManager(path)
            key = manager.get("server", "key")
            self.assertEqual(len(key), 32)
            int(key, 16)

    def test_load_is_lazy_and_creates_file_on_first_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "server.cfg")
            manager = ConfigManager(path)
            # 构造阶段不落盘（避免 import 副作用），首次取值才创建配置。
            self.assertFalse(os.path.exists(path))
            self.assertEqual(len(manager.get("server", "key")), 32)
            self.assertTrue(os.path.exists(path))

    def test_reload_keeps_existing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "server.cfg")
            manager = ConfigManager(path)
            key = manager.get("server", "key")
            manager.reload()
            self.assertEqual(manager.get("server", "key"), key)


class ConfigPeekTests(unittest.TestCase):
    """peek* 只读读取：不得创建/改写配置文件，供 import 期使用。"""

    def test_peek_does_not_create_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "server.cfg")
            manager = ConfigManager(path)
            # 根目录 mcp-endpoint-server.cfg 存在，peek 应读它且不落盘。
            self.assertEqual(manager.peek("server", "log_level", "INFO"), "INFO")
            self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_peek_falls_back_to_default_without_any_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConfigManager(os.path.join(tmp, "none.cfg"))
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(manager.peek("server", "log_level", "DEBUG"), "DEBUG")
                self.assertTrue(manager.peekboolean("security", "enable_cors", True))
                self.assertEqual(manager.peekint("logging", "backup_count", 7), 7)
            finally:
                os.chdir(cwd)
            self.assertEqual(os.listdir(tmp), [])

class NetworkUtilTests(unittest.TestCase):
    def test_get_local_ip_returns_string(self):
        value = get_local_ip()
        self.assertIsInstance(value, str)
        self.assertTrue(value)

    def test_get_local_ip_is_cached(self):
        self.assertEqual(get_local_ip(), get_local_ip())


if __name__ == "__main__":
    unittest.main()
