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

    def test_reload_keeps_existing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "server.cfg")
            manager = ConfigManager(path)
            key = manager.get("server", "key")
            manager.reload()
            self.assertEqual(manager.get("server", "key"), key)


class NetworkUtilTests(unittest.TestCase):
    def test_get_local_ip_returns_string(self):
        value = get_local_ip()
        self.assertIsInstance(value, str)
        self.assertTrue(value)


if __name__ == "__main__":
    unittest.main()
