from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


CNKI_INDEX_URL = "https://wap.cnki.net/touch/web/Article/SearchIndex/"
CNKI_SEARCH_API_URL = "https://wap.cnki.net/gate/m052/web/api/article/search"


def _load_env_value(lines: list[str], key: str) -> str:
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def load_cookie_from_file(env_path: str | Path, shard_id: int = 1) -> str:
    path = Path(env_path)
    if not path.exists():
        return ""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return _load_env_value(lines, f"CNKI_COOKIE_SHARED_{shard_id}") or _load_env_value(lines, "CNKI_COOKIE")



def load_cookie_from_env(shard_id: int = 1) -> str:
    return os.getenv(f"CNKI_COOKIE_SHARED_{shard_id}", "").strip() or os.getenv("CNKI_COOKIE", "").strip()


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    source_markdown: Path

    @property
    def output_dir(self) -> Path:
        return self.base_dir / "output"

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "cache"

    @property
    def raw_output_path(self) -> Path:
        return self.output_dir / "raw.jsonl"

    @property
    def enriched_output_path(self) -> Path:
        return self.output_dir / "enriched.jsonl"

    @property
    def articles_json_path(self) -> Path:
        return self.output_dir / "articles.json"

    @property
    def articles_csv_path(self) -> Path:
        return self.output_dir / "articles.csv"

    @property
    def citation_relations_csv_path(self) -> Path:
        return self.output_dir / "citation_relations.csv"
