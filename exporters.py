from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

RELATION_FIELDS = [
    "references",
    "citing_articles",
    "co_cited_articles",
    "co_citing_articles",
    "secondary_references",
    "secondary_citing_articles",
]

DETAIL_METADATA_FIELDS = [
    "institution",
    "keywords",
    "funding",
    "album",
    "special_topic",
    "classification_number",
]

ARTICLES_CSV_FIELDNAMES = [
    "article_id",
    "source_journal_input",
    "title",
    "authors",
    "journal",
    "publicationTime",
    "abstract",
    *DETAIL_METADATA_FIELDS,
    "quote_frequency",
    "download_count",
    "citation_network_status",
    "citation_network_error",
    *[field_name for relation_field in RELATION_FIELDS for field_name in (f"{relation_field}_count", f"{relation_field}_fetched_count")],
]

CITATION_RELATIONS_CSV_FIELDNAMES = [
    "article_id",
    "source_journal_input",
    "source_title",
    "source_authors",
    "source_journal",
    "source_publication_time",
    "relation_type",
    "target_title",
    "target_authors",
    "target_journal",
    "target_year",
    "target_file_name",
    "target_db_name",
    "target_db_type",
    "relation_rank",
]


def _ensure_csv_header(output_path: str | Path, fieldnames: list[str]) -> None:
    path = Path(output_path)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()


def export_article_jsonl(article: dict[str, Any], output_path: str | Path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(output_path).open("a", encoding="utf-8") as file:
        file.write(json.dumps(article, ensure_ascii=False) + "\n")


def export_articles_csv_row(article: dict[str, Any], output_path: str | Path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _ensure_csv_header(output_path, ARTICLES_CSV_FIELDNAMES)
    citation_metrics = article.get("citation_metrics", {})
    row = {
        "article_id": article.get("article_id", ""),
        "source_journal_input": article.get("source_journal_input", ""),
        "title": article.get("title", ""),
        "authors": article.get("authors", ""),
        "journal": article.get("journal", ""),
        "publicationTime": article.get("publicationTime", ""),
        "abstract": article.get("abstract", ""),
        **{field_name: article.get(field_name, "") for field_name in DETAIL_METADATA_FIELDS},
        "quote_frequency": article.get("quote_frequency", ""),
        "download_count": article.get("download_count", ""),
        "citation_network_status": article.get("citation_network_status", ""),
        "citation_network_error": article.get("citation_network_error", ""),
    }
    for relation_field in RELATION_FIELDS:
        fetched_count = len(article.get(relation_field, []))
        row[f"{relation_field}_count"] = str(citation_metrics[relation_field]) if relation_field in citation_metrics else ""
        row[f"{relation_field}_fetched_count"] = str(fetched_count)
    with Path(output_path).open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=ARTICLES_CSV_FIELDNAMES)
        writer.writerow(row)


def export_citation_relations_csv_row(article: dict[str, Any], output_path: str | Path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _ensure_csv_header(output_path, CITATION_RELATIONS_CSV_FIELDNAMES)
    article_id = article.get("article_id") or article.get("file_name") or article.get("title", "")
    rows: list[dict[str, Any]] = []
    for relation_field in RELATION_FIELDS:
        for index, target in enumerate(article.get(relation_field, []), start=1):
            rows.append({
                "article_id": article_id,
                "source_journal_input": article.get("source_journal_input", ""),
                "source_title": article.get("title", ""),
                "source_authors": article.get("authors", ""),
                "source_journal": article.get("journal", ""),
                "source_publication_time": article.get("publicationTime", ""),
                "relation_type": relation_field,
                "target_title": target.get("title", ""),
                "target_authors": target.get("authors", ""),
                "target_journal": target.get("journal", ""),
                "target_year": target.get("year", ""),
                "target_file_name": target.get("file_name", ""),
                "target_db_name": target.get("db_name", ""),
                "target_db_type": target.get("db_type", ""),
                "relation_rank": str(index),
            })
    if rows:
        with Path(output_path).open("a", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CITATION_RELATIONS_CSV_FIELDNAMES)
            writer.writerows(rows)


def export_article_incrementally(article: dict[str, Any], output_dir: str | Path) -> None:
    output_root = Path(output_dir)
    export_article_jsonl(article, output_root / "articles.jsonl")
    export_articles_csv_row(article, output_root / "articles.csv")
    export_citation_relations_csv_row(article, output_root / "citation_relations.csv")


# --- 保留全量导出，用于 --no-resume 或最终汇总 ---

def iter_jsonl_articles(input_path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(input_path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)



def export_all_formats_from_jsonl(input_path: str | Path, output_dir: str | Path) -> None:
    output_root = Path(output_dir)
    export_articles_json_stream(iter_jsonl_articles(input_path), output_root / "articles.json")
    export_articles_csv(iter_jsonl_articles(input_path), output_root / "articles.csv")
    export_citation_relations_csv(iter_jsonl_articles(input_path), output_root / "citation_relations.csv")



def export_articles_json_stream(articles: Iterable[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        file.write("[\n")
        first = True
        for article in articles:
            if not first:
                file.write(",\n")
            file.write(json.dumps(article, ensure_ascii=False, indent=2))
            first = False
        file.write("\n]\n")
    temp_path.replace(path)



def export_all_formats(articles: list[dict[str, Any]], output_dir: str | Path) -> None:
    output_root = Path(output_dir)
    export_articles_json(articles, output_root / "articles.json")
    export_articles_csv(articles, output_root / "articles.csv")
    export_citation_relations_csv(articles, output_root / "citation_relations.csv")



def export_articles_csv(articles: Iterable[dict[str, Any]], output_path: str | Path) -> None:
    """全量导出 CSV（兼容旧接口，先清空再写全部）"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=ARTICLES_CSV_FIELDNAMES)
        writer.writeheader()
        for article in articles:
            citation_metrics = article.get("citation_metrics", {})
            row = {
                "article_id": article.get("article_id", ""),
                "source_journal_input": article.get("source_journal_input", ""),
                "title": article.get("title", ""),
                "authors": article.get("authors", ""),
                "journal": article.get("journal", ""),
                "publicationTime": article.get("publicationTime", ""),
                "abstract": article.get("abstract", ""),
                **{field_name: article.get(field_name, "") for field_name in DETAIL_METADATA_FIELDS},
                "quote_frequency": article.get("quote_frequency", ""),
                "download_count": article.get("download_count", ""),
                "citation_network_status": article.get("citation_network_status", ""),
                "citation_network_error": article.get("citation_network_error", ""),
            }
            for relation_field in RELATION_FIELDS:
                fetched_count = len(article.get(relation_field, []))
                row[f"{relation_field}_count"] = str(citation_metrics[relation_field]) if relation_field in citation_metrics else ""
                row[f"{relation_field}_fetched_count"] = str(fetched_count)
            writer.writerow(row)


def export_citation_relations_csv(articles: Iterable[dict[str, Any]], output_path: str | Path) -> None:
    """全量导出引文关系 CSV（兼容旧接口，先清空再写全部）"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CITATION_RELATIONS_CSV_FIELDNAMES)
        writer.writeheader()
        for article in articles:
            article_id = article.get("article_id") or article.get("file_name") or article.get("title", "")
            for relation_field in RELATION_FIELDS:
                for index, target in enumerate(article.get(relation_field, []), start=1):
                    writer.writerow({
                        "article_id": article_id,
                        "source_journal_input": article.get("source_journal_input", ""),
                        "source_title": article.get("title", ""),
                        "source_authors": article.get("authors", ""),
                        "source_journal": article.get("journal", ""),
                        "source_publication_time": article.get("publicationTime", ""),
                        "relation_type": relation_field,
                        "target_title": target.get("title", ""),
                        "target_authors": target.get("authors", ""),
                        "target_journal": target.get("journal", ""),
                        "target_year": target.get("year", ""),
                        "target_file_name": target.get("file_name", ""),
                        "target_db_name": target.get("db_name", ""),
                        "target_db_type": target.get("db_type", ""),
                        "relation_rank": str(index),
                    })


def export_articles_json(articles: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
