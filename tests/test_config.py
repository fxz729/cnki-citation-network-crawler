import os
import tempfile
import unittest
from pathlib import Path

from config import AppConfig, load_cookie_from_env, load_cookie_from_file


class ConfigTests(unittest.TestCase):
    def test_load_cookie_from_env_returns_cookie_value(self):
        previous = os.environ.get("CNKI_COOKIE")
        os.environ["CNKI_COOKIE"] = "cookie=value"
        try:
            self.assertEqual(load_cookie_from_env(), "cookie=value")
        finally:
            if previous is None:
                os.environ.pop("CNKI_COOKIE", None)
            else:
                os.environ["CNKI_COOKIE"] = previous

    def test_load_cookie_from_env_returns_empty_string_when_missing(self):
        previous = os.environ.pop("CNKI_COOKIE", None)
        try:
            self.assertEqual(load_cookie_from_env(), "")
        finally:
            if previous is not None:
                os.environ["CNKI_COOKIE"] = previous

    def test_load_cookie_from_file_reads_env_style_cookie_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("CNKI_COOKIE=test-cookie\n", encoding="utf-8")

            self.assertEqual(load_cookie_from_file(env_path), "test-cookie")

    def test_load_cookie_from_env_prefers_shard_specific_cookie(self):
        previous_shared = os.environ.get("CNKI_COOKIE_SHARED_2")
        previous_default = os.environ.get("CNKI_COOKIE")
        os.environ["CNKI_COOKIE_SHARED_2"] = "shared-cookie"
        os.environ["CNKI_COOKIE"] = "default-cookie"
        try:
            self.assertEqual(load_cookie_from_env(shard_id=2), "shared-cookie")
        finally:
            if previous_shared is None:
                os.environ.pop("CNKI_COOKIE_SHARED_2", None)
            else:
                os.environ["CNKI_COOKIE_SHARED_2"] = previous_shared
            if previous_default is None:
                os.environ.pop("CNKI_COOKIE", None)
            else:
                os.environ["CNKI_COOKIE"] = previous_default

    def test_load_cookie_from_file_prefers_shard_specific_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "CNKI_COOKIE=default-cookie\nCNKI_COOKIE_SHARED_2=shared-cookie\n",
                encoding="utf-8",
            )

            self.assertEqual(load_cookie_from_file(env_path, shard_id=2), "shared-cookie")
            self.assertEqual(load_cookie_from_file(env_path, shard_id=1), "default-cookie")

    def test_cookie_line_preserves_json_cookie_and_following_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                'CNKI_COOKIE_SHARED_1=Ecp_LoginStuts={"IsAutoLogin":true}; LID=personal; SID_kns_new=session; SID_restapi=rest\n',
                encoding="utf-8",
            )

            cookie = load_cookie_from_file(env_path, shard_id=1)

        self.assertIn('Ecp_LoginStuts={"IsAutoLogin":true}', cookie)
        self.assertIn("LID=personal", cookie)
        self.assertIn("SID_kns_new=session", cookie)
        self.assertIn("SID_restapi=rest", cookie)


        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            config = AppConfig(base_dir=base_dir, source_markdown=base_dir / "journals.md")

        self.assertEqual(config.output_dir, base_dir / "output")
        self.assertEqual(config.cache_dir, base_dir / "cache")
        self.assertEqual(config.raw_output_path, base_dir / "output" / "raw.jsonl")
        self.assertEqual(config.enriched_output_path, base_dir / "output" / "enriched.jsonl")


if __name__ == "__main__":
    unittest.main()
