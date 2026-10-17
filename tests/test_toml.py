"""Unit tests for nix_adapter.toml."""

import unittest

from nix_adapter.toml import dump_toml, parse_toml


class TomlTests(unittest.TestCase):
    def test_parse_toml_string_and_bytes(self):
        doc = 'key = "value"\nnumber = 42\n[section]\nenabled = true\n'
        parsed = parse_toml(doc)
        self.assertEqual(parsed["key"], "value")
        self.assertEqual(parsed["number"], 42)
        self.assertEqual(parsed["section"]["enabled"], True)

        parsed_bytes = parse_toml(doc.encode("utf-8"))
        self.assertEqual(parsed_bytes, parsed)

    def test_dump_toml_roundtrip(self):
        original = {
            "title": "TOML Example",
            "count": 10,
            "ratio": 3.14,
            "enabled": True,
            "items": ["apple", "banana"],
            "server": {"host": "127.0.0.1", "port": 8080},
            "clients": [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
            "special.key": "dotted",
        }
        dumped = dump_toml(original)
        reloaded = parse_toml(dumped)
        self.assertEqual(reloaded, original)


if __name__ == "__main__":
    unittest.main()
