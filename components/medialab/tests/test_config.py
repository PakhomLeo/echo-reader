import unittest
import os
import tempfile
from pathlib import Path
from unittest import mock

from medialab.config import load_config


class TestConfig(unittest.TestCase):
    def test_default_load(self):
        with mock.patch.dict("os.environ", {"LIBRARY_ROOT": ""}):
            cfg = load_config()
        self.assertEqual(cfg["concurrency"]["max_concurrent"], 3)
        self.assertIn("bilibili.*", cfg["platforms"]["enabled"])
        self.assertEqual(Path(cfg["paths"]["delivery"]), Path(__file__).resolve().parents[3] / "content")

    def test_overrides_merge(self):
        cfg = load_config(overrides={"concurrency": {"max_concurrent": 7}})
        self.assertEqual(cfg["concurrency"]["max_concurrent"], 7)
        self.assertEqual(cfg["download"]["retries"], 10)

    def test_nested_override_keeps_siblings(self):
        cfg = load_config(overrides={"download": {"retries": 5}})
        self.assertEqual(cfg["download"]["retries"], 5)
        self.assertEqual(cfg["download"]["fragment_retries"], 10)

    def test_explicit_secrets_path_does_not_read_install_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            explicit = root / "app-config" / "secrets.toml"
            explicit.parent.mkdir()
            explicit.write_text('[asr]\napi_key = "explicit"\n', encoding="utf-8")
            cwd = root / "install-cwd"
            (cwd / "config").mkdir(parents=True)
            (cwd / "config" / "secrets.toml").write_text(
                '[asr]\napi_key = "wrong-cwd"\n', encoding="utf-8",
            )
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                cfg = load_config(secrets_path=explicit)
            finally:
                os.chdir(previous)
        self.assertEqual(cfg["asr"]["api_key"], "explicit")


if __name__ == "__main__":
    unittest.main()
