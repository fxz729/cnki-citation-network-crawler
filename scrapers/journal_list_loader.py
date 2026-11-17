from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


class _JournalTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._current_cell_parts: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "td":
            self._in_td = True
            self._current_cell_parts = []
        elif tag.lower() == "tr":
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._current_cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "td":
            cell_text = "".join(self._current_cell_parts).strip()
            self._current_row.append(cell_text)
            self._current_cell_parts = []
            self._in_td = False
        elif tag_name == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = []


def _append_journal_name(journal_names: list[str], seen: set[str], journal_name: str) -> None:
    normalized_name = journal_name.strip()
    if not normalized_name or normalized_name in {"期刊名称", "期刊原名"} or normalized_name in seen:
        return
    seen.add(normalized_name)
    journal_names.append(normalized_name)


def load_journal_names(file_path: str | Path) -> list[str]:
    content = Path(file_path).read_text(encoding="utf-8")
    parser = _JournalTableParser()
    parser.feed(content)

    journal_names: list[str] = []
    seen: set[str] = set()
    for row in parser.rows:
        if len(row) >= 2:
            _append_journal_name(journal_names, seen, row[1])

    markdown_journal_column: int | None = None
    for line in content.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        row = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "期刊原名" in row:
            markdown_journal_column = row.index("期刊原名")
            continue
        if markdown_journal_column is None or len(row) <= markdown_journal_column:
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in row):
            continue
        _append_journal_name(journal_names, seen, row[markdown_journal_column])

    return journal_names
