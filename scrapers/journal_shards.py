from __future__ import annotations


def split_journals(journals: list[str], shard_count: int) -> list[list[str]]:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    return [journals[index::shard_count] for index in range(shard_count)]


def select_journal_shard(journals: list[str], shard_id: int, shard_count: int) -> list[str]:
    if shard_id < 1 or shard_id > shard_count:
        raise ValueError("shard_id must be between 1 and shard_count")
    return split_journals(journals, shard_count)[shard_id - 1]
