import json
import tempfile
import unittest
from pathlib import Path

from scrapers.checkpoint import load_checkpoint, save_checkpoint


class CheckpointTests(unittest.TestCase):
    def test_save_checkpoint_uses_unique_private_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.json"
            save_checkpoint(path, {"value": 1})

            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], 1)

    def test_load_checkpoint_returns_empty_shape_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = load_checkpoint(Path(temp_dir) / "checkpoint.json")

        self.assertEqual(checkpoint["completed_journals"], [])
        self.assertEqual(checkpoint["current_journal"], "")

    def test_save_checkpoint_writes_json_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.json"
            data = {
                "shard_id": 1,
                "shard_count": 3,
                "completed_journals": ["期刊A"],
            }

            save_checkpoint(path, data)
            loaded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(loaded, data)


if __name__ == "__main__":
    unittest.main()
