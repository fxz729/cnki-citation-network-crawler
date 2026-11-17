"""Isolated standard-library tests for the pilot JSON store."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from scrapers.pilot_store import PilotStore
except ModuleNotFoundError as error:
    if error.name != "scrapers.pilot_store":
        raise
    PilotStore = None


class PilotStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PilotStore, "PilotStore implementation is missing")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "store"
        self.store = PilotStore(self.root)

    def path(self, bucket="plans", key="key"):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / bucket / (digest + ".json")

    def test_roundtrip_reopen_items_and_delete(self):
        for bucket in ("plans", "relations", "retries", "metrics"):
            with self.subTest(bucket=bucket):
                self.assertIsNone(self.store.get(bucket, "key"))
                self.assertEqual(self.store.items(bucket), [])
                self.store.put(bucket, "key", {"nested": [1, None, {"ok": True}]})
                self.store.put(bucket, "empty", {})
                reopened = PilotStore(self.root)
                self.assertEqual(reopened.get(bucket, "key"),
                                 {"nested": [1, None, {"ok": True}]})
                self.assertEqual(dict(reopened.items(bucket)),
                                 {"key": reopened.get(bucket, "key"), "empty": {}})
                reopened.delete(bucket, "key")
                reopened.delete(bucket, "key")
                self.assertIsNone(reopened.get(bucket, "key"))
                self.assertEqual(reopened.items(bucket), [("empty", {})])

    def test_bucket_validation_and_hashed_path_traversal_keys(self):
        for bucket in ("", "../plans", "plans/../metrics", "/tmp", "PLANS", None):
            for operation in (lambda: self.store.get(bucket, "key"),
                              lambda: self.store.put(bucket, "key", {}),
                              lambda: self.store.delete(bucket, "key"),
                              lambda: self.store.items(bucket)):
                with self.subTest(bucket=bucket), self.assertRaises(ValueError):
                    operation()
        for key in ("../../escape", "..\\escape", "/absolute", "", "中文键"):
            self.store.put("plans", key, {"value": key})
            self.assertTrue(self.path(key=key).is_file())
            self.assertEqual(self.store.get("plans", key), {"value": key})
        self.assertEqual(len(list((self.root / "plans").iterdir())), 5)

    def test_utf8_envelope_and_payload_not_filtered(self):
        value = {"标题": "中文内容", "token": "", "url": None, "arbitrary": [1]}
        self.store.put("plans", "中文", value)
        raw = self.path(key="中文").read_bytes()
        self.assertIn("中文内容".encode("utf-8"), raw)
        self.assertEqual(json.loads(raw), {"version": 1, "key": "中文", "payload": value})
        self.assertEqual(self.store.get("plans", "中文"), value)

    def test_corruption_unknown_schema_retained_and_skipped(self):
        invalid = [b'{"private-marker":', b'\xff', b'[]', b'null']
        for change in ({"version": 2}, {"version": True}, {"version": 1.0},
                       {"payload": []}, {"key": "wrong-private-marker"},
                       {"extra": "private-marker"}):
            doc = {"version": 1, "key": "key", "payload": {}}
            doc.update(change)
            invalid.append(json.dumps(doc).encode())
        invalid.append(b'{"version":1,"key":"key"}')
        self.store.put("plans", "good", {"ok": True})
        path = self.path()
        for raw in invalid:
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertLogs("scrapers.pilot_store", level="WARNING") as logs:
                    self.assertIsNone(self.store.get("plans", "key"))
                    self.assertEqual(self.store.items("plans"), [("good", {"ok": True})])
                self.assertEqual(path.read_bytes(), raw)
                for record in logs.records:
                    message = record.getMessage()
                    self.assertIn(str(path), message)
                    self.assertNotIn("private-marker", message)
                    self.assertIsNone(record.exc_info)
        self.store.put("plans", "key", {"repaired": True})
        self.assertEqual(self.store.get("plans", "key"), {"repaired": True})
        path.write_bytes(b"broken")
        self.store.delete("plans", "key")
        self.assertFalse(path.exists())

    def test_items_reject_misnamed_document(self):
        self.store.put("plans", "key", {})
        self.path().rename(self.path(key="other"))
        with self.assertLogs("scrapers.pilot_store", level="WARNING"):
            self.assertEqual(self.store.items("plans"), [])
            self.assertIsNone(self.store.get("plans", "other"))
        self.assertTrue(self.path(key="other").exists())

    def test_replace_failure_preserves_old_or_absent_document_and_cleans_temp(self):
        self.store.put("plans", "key", {"old": True})
        previous = self.path().read_bytes()
        for key in ("key", "new"):
            with patch("scrapers.pilot_store.os.replace", side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    self.store.put("plans", key, {"new": True})
            self.assertEqual(self.path().read_bytes(), previous)
            self.assertFalse(self.path(key="new").exists())
            self.assertEqual(list((self.root / "plans").iterdir()), [self.path()])

    def test_fsync_failure_before_replace_preserves_old_document(self):
        self.store.put("plans", "key", {"old": True})
        with patch("scrapers.pilot_store.os.fsync", side_effect=OSError("failure")), \
                patch("scrapers.pilot_store.os.replace") as replace:
            with self.assertRaises(OSError):
                self.store.put("plans", "key", {"new": True})
        replace.assert_not_called()
        self.assertEqual(self.store.get("plans", "key"), {"old": True})
        self.assertEqual(list((self.root / "plans").iterdir()), [self.path()])

    def test_invalid_input_does_not_overwrite(self):
        self.store.put("plans", "key", {"old": True})
        for value in ([], {"unsupported": object()}, {"nan": float("nan")}):
            with self.assertRaises((TypeError, ValueError)):
                self.store.put("plans", "key", value)
            self.assertEqual(self.store.get("plans", "key"), {"old": True})
        for operation in (self.store.get, self.store.delete):
            with self.assertRaises(TypeError):
                operation("plans", 1)
        with self.assertRaises(TypeError):
            self.store.put("plans", 1, {})
        self.assertEqual(list((self.root / "plans").iterdir()), [self.path()])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux permission semantics")
    def test_linux_permissions_under_permissive_umask(self):
        old_umask = os.umask(0)
        try:
            store = PilotStore(self.root / "linux")
            for bucket in ("plans", "relations", "retries", "metrics"):
                store.put(bucket, "key", {})
                store.put(bucket, "key", {"updated": True})
        finally:
            os.umask(old_umask)
        for path in (self.root / "linux", *(self.root / "linux").rglob("*")):
            expected = 0o700 if path.is_dir() else 0o600
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path))


if __name__ == "__main__":
    unittest.main()
