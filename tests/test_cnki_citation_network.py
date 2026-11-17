import unittest

from scrapers.cnki_citation_network import (
    CITATION_RELATION_TYPES,
    build_article_id,
    build_citation_list_request,
    build_citation_metrics_request,
    build_detail_context,
    build_detail_urls,
    build_reader_detail_request,
    build_reader_references_request,
    extract_metadata_value,
    normalize_relation_items,
    parse_citation_metrics,
)


class CitationNetworkTests(unittest.TestCase):
    def test_build_article_id_prefers_file_name_and_db_name(self):
        article_id = build_article_id({"file_name": "JYPL202601001", "db_name": "CJFDLAST2026", "title": "文章A"})
        self.assertEqual(article_id, "JYPL202601001:CJFDLAST2026")

    def test_build_detail_urls_uses_file_name_and_db_name(self):
        urls = build_detail_urls({"file_name": "JYPL202601001", "db_name": "CJFDLAST2026"})
        self.assertIn("kns", urls)
        self.assertIn("html", urls)
        self.assertIn("JYPL202601001", urls["html"])
        self.assertIn("CJFDLAST2026", urls["html"])

    def test_build_detail_context_extracts_abstract_from_detail_html(self):
        html = """
        <input id="apiHost" type="hidden" value="https://kns.cnki.net/restapi"/>
        <input id="paramkcmslink" type="hidden" value="v=test&amp;uniplatform=NZKPT"/>
        <input id="paramfilename" type="hidden" value="A1"/>
        <input id="paramdbcode" type="hidden" value="CJFQ"/>
        <span id="ChDivSummary">摘要：城市电商化转型会影响企业税收遵从。</span>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["kcms_link"], "v=test&uniplatform=NZKPT")
        self.assertEqual(context["filename"], "A1")
        self.assertEqual(context["dbcode"], "CJFQ")
        self.assertEqual(context["abstract"], "城市电商化转型会影响企业税收遵从。")

    def test_build_detail_context_extracts_abstract_when_label_and_text_are_separate(self):
        html = """
        <div class="row">
          <span>摘要：</span><span>城市电商化转型会影响企业税收遵从。</span>
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["abstract"], "城市电商化转型会影响企业税收遵从。")

    def test_build_detail_context_extracts_detail_metadata_fields(self):
        html = """
        <input id="paramkcmslink" type="hidden" value="v=test&amp;uniplatform=NZKPT"/>
        <div class="wxBaseinfo">
          <p><span>作者单位：</span>北京大学教育学院</p>
          <p><span>关键词：</span><a>教育治理</a>；<a>数字化转型</a></p>
          <p><span>基金资助：</span>国家社会科学基金项目</p>
          <p><span>专辑：</span>社会科学Ⅱ辑</p>
          <p><span>专题：</span>教育理论与教育管理</p>
          <p><span>分类号：</span>G40</p>
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["institution"], "北京大学教育学院")
        self.assertEqual(context["keywords"], "教育治理；数字化转型")
        self.assertEqual(context["funding"], "国家社会科学基金项目")
        self.assertEqual(context["album"], "社会科学Ⅱ辑")
        self.assertEqual(context["special_topic"], "教育理论与教育管理")
        self.assertEqual(context["classification_number"], "G40")
        self.assertEqual(context["kcms_link"], "v=test&uniplatform=NZKPT")

    def test_build_detail_context_extracts_institution_when_label_and_links_are_separate(self):
        html = """
        <div class="wxBaseinfo">
          <p><span>作者单位</span><span>：</span><a>北京大学教育学院</a>；<a>清华大学教育研究院</a></p>
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["institution"], "北京大学教育学院；清华大学教育研究院")

    def test_build_detail_context_extracts_institution_from_author_affiliation_links(self):
        html = """
        <div class="author-info">
          <p class="authors"><a>张三</a><a>李四</a></p>
          <p class="affiliations"><a>北京大学教育学院</a>；<a>清华大学教育研究院</a></p>
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["institution"], "北京大学教育学院；清华大学教育研究院")

    def test_build_detail_context_extracts_institution_from_cnki_organ_detail_links(self):
        html = """
        <div class="wx-tit">
          <h1>文章标题</h1>
          <h3 class="author" id="authorpart">
            <span><a href="https://kns.cnki.net/kcms2/author/detail?v=author1">张三<sup>1</sup></a></span>
            <span><a href="https://kns.cnki.net/kcms2/author/detail?v=author2">李四<sup>2</sup></a></span>
          </h3>
          <h3 class="author">
            <span><a href="https://kns.cnki.net/kcms2/organ/detail?v=org1">1.北京大学教育学院</a></span>
            <span><a href="https://kns.cnki.net/kcms2/organ/detail?v=org2">2.清华大学教育研究院</a></span>
          </h3>
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["institution"], "北京大学教育学院；清华大学教育研究院")

    def test_build_detail_context_does_not_treat_body_unit_text_as_institution(self):
        html = """
        <div class="abstract">
          摘要：本文以万元为计量单位：分析高校经费投入，基金支持不是元数据字段。
        </div>
        """

        context = build_detail_context({"file_name": "A1", "db_name": "CJFQ"}, html)

        self.assertEqual(context["institution"], "")
        self.assertEqual(context["funding"], "")

    def test_build_detail_context_keeps_existing_metadata_when_detail_html_omits_it(self):
        html = """
        <input id="paramkcmslink" type="hidden" value="v=test&amp;uniplatform=NZKPT"/>
        <span id="ChDivSummary">摘要：详情页摘要。</span>
        """

        context = build_detail_context(
            {"file_name": "A1", "db_name": "CJFQ", "institution": "已有单位", "keywords": "已有关键词"},
            html,
        )

        self.assertEqual(context["institution"], "已有单位")
        self.assertEqual(context["keywords"], "已有关键词")
        self.assertEqual(context["abstract"], "详情页摘要。")

    def test_build_citation_list_request_uses_page_offset_and_page_size(self):
        article = {
            "detail_context": {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test-token&uniplatform=NZKPT",
            }
        }

        request = build_citation_list_request(article, relation_type="references", file_type="CJFDREF", page=2, page_size=50)

        self.assertEqual(
            request["url"],
            "https://kns.cnki.net/restapi/citation-api/v1/literature/references?v=test-token&uniplatform=NZKPT&type=CJFDREF&start=51&size=50",
        )

    def test_build_citation_list_request_uses_restapi_reference_endpoint(self):
        article = {
            "file_name": "BJPL202602001",
            "detail_context": {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test-token&uniplatform=NZKPT",
            },
        }

        request = build_citation_list_request(article, relation_type="references", file_type="CJFQ")

        self.assertEqual(
            request["url"],
            "https://kns.cnki.net/restapi/citation-api/v1/literature/references?v=test-token&uniplatform=NZKPT&type=CJFQ&start=1&size=10",
        )

    def test_build_citation_metrics_request_uses_metrics_endpoint(self):
        article = {
            "detail_context": {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test-token&uniplatform=NZKPT",
            }
        }

        request = build_citation_metrics_request(article)

        self.assertEqual(
            request["url"],
            "https://kns.cnki.net/restapi/citation-api/v1/literature/quotations/metrics?v=test-token&uniplatform=NZKPT",
        )

    def test_extract_metadata_value_finds_reader_vid(self):
        payload = {
            "metadata": [
                {"name": "TI", "value": "文章A"},
                {"name": "vid", "value": "reader-token"},
            ]
        }

        self.assertEqual(extract_metadata_value(payload, "vid"), "reader-token")

    def test_extract_metadata_value_reads_detail_context_v_from_kcms_link(self):
        payload = {"kcms_link": "v=detail-token%2Babc&uniplatform=NZKPT"}

        self.assertEqual(extract_metadata_value(payload, "vid"), "detail-token+abc")

    def test_build_reader_references_request_uses_original_references_auth_endpoint(self):
        request = build_reader_references_request("reader-token", page=1, page_size=50)

        self.assertEqual(
            request["url"],
            "https://kns.cnki.net/restapi/citation-api/v1/literature/original-references-auth?size=50&start=1&v=reader-token",
        )
        self.assertIn("application/json", request["headers"]["Accept"])

    def test_build_reader_references_request_encodes_vid(self):
        request = build_reader_references_request("a+b&c=d", page=1, page_size=50)

        self.assertIn("v=a%2Bb%26c%3Dd", request["url"])

    def test_build_reader_detail_request_uses_reader_payload_shape(self):
        request = build_reader_detail_request(
            {
                "file_name": "ADZS202306012",
                "db_name": "CJFQ",
                "db_type": "JOURNAL",
                "detail_context": {"dbname": "cjfdlast2024"},
            }
        )

        self.assertIn("/literature-api/v1/articles/detail?", request["url"])
        self.assertEqual(
            request["json"],
            {
                "id": "ADZS202306012",
                "resource": "CJFQ",
                "type": "JOURNAL",
                "dataset": "cjfdlast2024",
                "start": 1,
                "size": 10,
            },
        )

    def test_parse_citation_metrics_maps_all_relation_counts(self):
        payload = {
            "code": 0,
            "data": {
                "references": 8,
                "citations": 5,
                "coreferences": 3,
                "cocitations": 2,
                "secondreferences": 1,
                "secondcitations": 4,
            },
        }

        metrics = parse_citation_metrics(payload)

        self.assertEqual(metrics["references"], 8)
        self.assertEqual(metrics["citing_articles"], 5)
        self.assertEqual(metrics["co_cited_articles"], 3)
        self.assertEqual(metrics["co_citing_articles"], 2)
        self.assertEqual(metrics["secondary_references"], 1)
        self.assertEqual(metrics["secondary_citing_articles"], 4)

    def test_normalize_relation_items_maps_common_cnki_fields(self):
        items = [
            {
                "title": "参考文献A",
                "author": "李四",
                "publishName": "教育研究",
                "year": "2020",
                "fileName": "JYYJ202001001",
                "dBName": "CJFDLAST2020",
                "type": "1",
            }
        ]

        normalized = normalize_relation_items(items)

        self.assertEqual(normalized[0]["title"], "参考文献A")
        self.assertEqual(normalized[0]["authors"], "李四")
        self.assertEqual(normalized[0]["journal"], "教育研究")
        self.assertEqual(normalized[0]["year"], "2020")
        self.assertEqual(normalized[0]["file_name"], "JYYJ202001001")
        self.assertEqual(normalized[0]["db_name"], "CJFDLAST2020")
        self.assertEqual(normalized[0]["db_type"], "1")

    def test_normalize_relation_items_accepts_author_list_shape(self):
        items = [
            {
                "metadata": [
                    {"name": "TI", "value": "参考文献A"},
                    {"name": "LY", "value": "教育研究"},
                    {"name": "YE", "value": "2020"},
                    {"name": "FN", "value": "JYYJ202001001"},
                    {"name": "TN", "value": "CJFDLAST2020"},
                    {"name": "resource", "value": "JOURNAL"},
                ],
                "authors": [{"name": "李四"}, {"name": "王五"}],
            }
        ]

        normalized = normalize_relation_items(items)

        self.assertEqual(normalized[0]["authors"], "李四;王五")

    def test_normalize_relation_items_tolerates_none_author_names(self):
        items = [
            {
                "metadata": [
                    {"name": "TI", "value": "参考文献A"},
                    {"name": "LY", "value": "教育研究"},
                    {"name": "YE", "value": "2020"},
                    {"name": "FN", "value": "JYYJ202001001"},
                    {"name": "TN", "value": "CJFDLAST2020"},
                    {"name": "resource", "value": "JOURNAL"},
                ],
                "authors": [{"name": None}, {"name": "王五"}, None],
            }
        ]

        normalized = normalize_relation_items(items)

        self.assertEqual(normalized[0]["authors"], "王五")

    def test_normalize_relation_items_uses_cnki_author_text_fallbacks(self):
        items = [
            {"title": "参考文献A", "authorText": "李四，王五；赵六"},
            {"title": "参考文献B", "showAuthor": [{"name": "孙七"}, "周八;"]},
            {"title": "参考文献C", "metadata": [{"name": "AU", "value": ""}], "creatorName": "吴九, 郑十"},
        ]

        normalized = normalize_relation_items(items)

        self.assertEqual(normalized[0]["authors"], "李四;王五;赵六")
        self.assertEqual(normalized[1]["authors"], "孙七;周八")
        self.assertEqual(normalized[2]["authors"], "吴九;郑十")

    def test_citation_relation_types_cover_all_six_categories(self):
        self.assertEqual(
            set(CITATION_RELATION_TYPES),
            {"references", "citations", "coreferences", "cocitations", "secondreferences", "secondcitations"},
        )


if __name__ == "__main__":
    unittest.main()
