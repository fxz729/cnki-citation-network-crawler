from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None


def empty_checkpoint() -> dict[str, Any]:
    return {
        "shard_id": 1,
        "shard_count": 1,
        "current_journal": "",
        "completed_journals": [],
        "updated_at": "",
    }


def load_checkpoint(path: str | Path) -> dict[str, Any]:
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return empty_checkpoint()
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint = empty_checkpoint()
    checkpoint.update(data)
    if not isinstance(checkpoint.get("completed_journals"), list):
        checkpoint["completed_journals"] = []
    return checkpoint


@contextmanager
def checkpoint_lock(path: str | Path):
    lock_path = Path(path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def save_checkpoint(path: str | Path, data: dict[str, Any]) -> None:
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_lock(checkpoint_path):
        _save_checkpoint_unlocked(checkpoint_path, data)


def _save_checkpoint_unlocked(checkpoint_path: Path, data: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{checkpoint_path.name}.",
        suffix=".tmp",
        dir=checkpoint_path.parent,
    )
    try:
        os.chmod(temp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        Path(temp_name).replace(checkpoint_path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def stamp_checkpoint(data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
