"""Small JSON store; callers must exclude credentials and URLs from payloads."""
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)
_BUCKETS = ("plans", "relations", "retries", "metrics")


def _reject_constant(_value):
    raise ValueError()


class PilotStore:
    """Atomic per-key writes, with last successful replacement winning."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for bucket in _BUCKETS:
            directory = self.root / bucket
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        self._sync_directory(self.root)
        self._sync_directory(self.root.parent)

    @staticmethod
    def _sync_directory(directory: Path):
        # Windows does not support opening directories for fsync this way.
        if os.name == "posix":
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _bucket(self, bucket: str) -> Path:
        if not isinstance(bucket, str) or bucket not in _BUCKETS:
            raise ValueError("Invalid bucket")
        return self.root / bucket

    @staticmethod
    def _filename(key: str) -> str:
        if not isinstance(key, str):
            raise TypeError("Key must be a string")
        return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"

    def _read(self, path: Path) -> dict | None:
        try:
            with path.open(encoding="utf-8") as stream:
                doc = json.load(stream, parse_constant=_reject_constant)
            if (not isinstance(doc, dict)
                    or set(doc) != {"version", "key", "payload"}
                    or type(doc["version"]) is not int or doc["version"] != 1
                    or not isinstance(doc["key"], str)
                    or not isinstance(doc["payload"], dict)
                    or self._filename(doc["key"]) != path.name):
                raise ValueError()
            return doc
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError) as error:
            logger.warning("%s: %s", type(error).__name__, path)
            return None

    def get(self, bucket: str, key: str) -> dict | None:
        doc = self._read(self._bucket(bucket) / self._filename(key))
        return None if doc is None else doc["payload"]

    def put(self, bucket: str, key: str, value: dict) -> None:
        path = self._bucket(bucket) / self._filename(key)
        if not isinstance(value, dict):
            raise TypeError("Payload must be a dict")
        content = json.dumps({"version": 1, "key": key, "payload": value},
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pilot-",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                temporary.chmod(0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, bucket: str, key: str) -> None:
        path = self._bucket(bucket) / self._filename(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self._sync_directory(path.parent)

    def items(self, bucket: str) -> list[tuple[str, dict]]:
        result = []
        for path in sorted(self._bucket(bucket).glob("*.json")):
            doc = self._read(path)
            if doc is not None:
                result.append((doc["key"], doc["payload"]))
        return result
