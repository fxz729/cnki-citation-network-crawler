import csv
import json
import tempfile
import unittest
from pathlib import Path

from exporters import export_articles_json, export_articles_csv, export_citation_relations_csv


class ExportersTests(unittest.TestCase):
    def test_export_articles_json_preserves_nested_citation_lists(self):
        articles = [
            {
                "title": "文章A",
                "authors": "张三",
                "journal": "北京大学教育评论",
                "references": [{"title": "参考文献1", "year": "2020"}],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "articles.json"
            export_articles_json(articles, output_path)
            data = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(data[0]["references"][0]["title"], "参考文献1")

    def test_export_articles_csv_writes_metrics_counts_and_fetched_counts(self):
        articles = [
            {
                "article_id": "A1:DB1",
                "source_journal_input": "北京大学教育评论",
                "title": "文章A",
                "authors": "张三",
                "journal": "北京大学教育评论",
                "publicationTime": "2026-01-01",
                "abstract": "文章摘要",
                "quote_frequency": "12",
                "download_count": "8",
                "citation_network_status": "success",
                "citation_network_error": "",
                "citation_metrics": {"references": 23, "citing_articles": 5},
                "references": [{"title": "参考文献1"}, {"title": "参考文献2"}],
                "citing_articles": [{"title": "引证文献1"}],
                "co_cited_articles": [],
                "co_citing_articles": [],
                "secondary_references": [],
                "secondary_citing_articles": [],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "articles.csv"
            export_articles_csv(articles, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(rows[0]["article_id"], "A1:DB1")
        self.assertEqual(rows[0]["abstract"], "文章摘要")
        self.assertEqual(rows[0]["citation_network_status"], "success")
        self.assertEqual(rows[0]["references_count"], "23")
        self.assertEqual(rows[0]["references_fetched_count"], "2")
        self.assertEqual(rows[0]["citing_articles_count"], "5")
        self.assertEqual(rows[0]["citing_articles_fetched_count"], "1")

    def test_export_articles_csv_writes_detail_metadata_fields(self):
        articles = [
            {
                "article_id": "A1:DB1",
                "title": "文章A",
                "institution": "北京大学教育学院",
                "keywords": "教育治理；数字化转型",
                "funding": "国家社会科学基金项目",
                "album": "社会科学Ⅱ辑",
                "special_topic": "教育理论与教育管理",
                "classification_number": "G40",
                "references": [],
                "citing_articles": [],
                "co_cited_articles": [],
                "co_citing_articles": [],
                "secondary_references": [],
                "secondary_citing_articles": [],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "articles.csv"
            export_articles_csv(articles, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(rows[0]["institution"], "北京大学教育学院")
        self.assertEqual(rows[0]["keywords"], "教育治理；数字化转型")
        self.assertEqual(rows[0]["funding"], "国家社会科学基金项目")
        self.assertEqual(rows[0]["album"], "社会科学Ⅱ辑")
        self.assertEqual(rows[0]["special_topic"], "教育理论与教育管理")
        self.assertEqual(rows[0]["classification_number"], "G40")

    def test_export_articles_csv_leaves_metrics_count_empty_when_metrics_missing(self):
        articles = [
            {
                "title": "文章A",
                "references": [{"title": "参考文献1"}, {"title": "参考文献2"}],
                "citing_articles": [],
                "co_cited_articles": [],
                "co_citing_articles": [],
                "secondary_references": [],
                "secondary_citing_articles": [],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "articles.csv"
            export_articles_csv(articles, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(rows[0]["references_count"], "")
        self.assertEqual(rows[0]["references_fetched_count"], "2")

    def test_export_articles_csv_keeps_count_only_relations_at_zero_fetched(self):
        articles = [
            {
                "title": "文章A",
                "citation_metrics": {
                    "co_cited_articles": 9,
                    "co_citing_articles": 8,
                    "secondary_references": 7,
                    "secondary_citing_articles": 6,
                },
                "references": [],
                "citing_articles": [],
                "co_cited_articles": [],
                "co_citing_articles": [],
                "secondary_references": [],
                "secondary_citing_articles": [],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "articles.csv"
            export_articles_csv(articles, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(rows[0]["co_cited_articles_count"], "9")
        self.assertEqual(rows[0]["co_cited_articles_fetched_count"], "0")
        self.assertEqual(rows[0]["co_citing_articles_count"], "8")
        self.assertEqual(rows[0]["co_citing_articles_fetched_count"], "0")
        self.assertEqual(rows[0]["secondary_references_count"], "7")
        self.assertEqual(rows[0]["secondary_references_fetched_count"], "0")
        self.assertEqual(rows[0]["secondary_citing_articles_count"], "6")
        self.assertEqual(rows[0]["secondary_citing_articles_fetched_count"], "0")

    def test_export_citation_relations_csv_flattens_all_relation_rows(self):
        articles = [
            {
                "article_id": "A1",
                "source_journal_input": "北京大学教育评论",
                "title": "文章A",
                "authors": "张三",
                "journal": "北京大学教育评论",
                "publicationTime": "2026-01-01",
                "references": [{"title": "参考文献1", "authors": "李四", "journal": "教育研究", "year": "2020"}],
                "citing_articles": [{"title": "引证文献1", "authors": "王五", "journal": "高等教育研究", "year": "2021"}],
                "co_cited_articles": [],
                "co_citing_articles": [],
                "secondary_references": [],
                "secondary_citing_articles": [],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "citation_relations.csv"
            export_citation_relations_csv(articles, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["relation_type"], "references")
        self.assertEqual(rows[1]["relation_type"], "citing_articles")


if __name__ == "__main__":
    unittest.main()
