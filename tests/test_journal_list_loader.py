import tempfile
import unittest
from pathlib import Path

from scrapers.journal_list_loader import load_journal_names


class JournalListLoaderTests(unittest.TestCase):
    def test_load_journal_names_extracts_second_column_from_html_table(self):
        markdown = """# 标题

说明文字

<table>
<tr><td>序号</td><td>期刊名称</td><td>学科名称</td></tr>
<tr><td>1</td><td>北京大学教育评论</td><td>教育学</td></tr>
<tr><td>2</td><td>清华大学教育研究</td><td>教育学</td></tr>
</table>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "full.md"
            file_path.write_text(markdown, encoding="utf-8")

            journal_names = load_journal_names(file_path)

        self.assertEqual(journal_names, ["北京大学教育评论", "清华大学教育研究"])

    def test_load_journal_names_deduplicates_and_skips_invalid_rows(self):
        markdown = """<table>
<tr><td>序号</td><td>期刊名称</td><td>学科名称</td></tr>
<tr><td>1</td><td>北京大学教育评论</td><td>教育学</td></tr>
<tr><td>2</td><td></td><td>教育学</td></tr>
<tr><td>3</td><td>北京大学教育评论</td><td>教育学</td></tr>
<tr><td>4</td><td>  清华大学教育研究  </td><td>教育学</td></tr>
</table>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "full.md"
            file_path.write_text(markdown, encoding="utf-8")

            journal_names = load_journal_names(file_path)

        self.assertEqual(journal_names, ["北京大学教育评论", "清华大学教育研究"])

    def test_load_journal_names_extracts_journal_original_name_from_markdown_table(self):
        markdown = """# CSSCI 来源刊公众号主页链接

| 序号 | 学科 | 类别 | 期刊原名 | 搜索到的公众号作者 | 公众号主页链接 | 样本文章标题 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 教育学 | cssci | 北京大学教育评论 | 北京大学教育评论 | https://example.test/1 | 样本一 |
| 2 | 教育学 | cssci | 清华大学教育研究 | 清华大学教育研究 | https://example.test/2 | 样本二 |
| 3 | 教育学 | cssci | 北京大学教育评论 | 北京大学教育评论 | https://example.test/3 | 样本三 |
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "wechat.md"
            file_path.write_text(markdown, encoding="utf-8")

            journal_names = load_journal_names(file_path)

        self.assertEqual(journal_names, ["北京大学教育评论", "清华大学教育研究"])
    def test_load_cssci_wechat_source_contains_126_unique_journals(self):
        source_path = Path(__file__).resolve().parents[1] / "data" / "CSSCI来源刊公众号主页链接.md"

        journal_names = load_journal_names(source_path)

        self.assertEqual(len(journal_names), 126)
        self.assertEqual(len(set(journal_names)), 126)
        self.assertEqual(journal_names[0], "当代世界社会主义问题")
        self.assertEqual(journal_names[-1], "江苏社会科学")


if __name__ == "__main__":
    unittest.main()
