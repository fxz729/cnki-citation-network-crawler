import json
import unittest

from scrapers.source_search_scraper import build_brief_grid_payload, fetch_search_page, parse_brief_grid_response


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, data=None, json=None, timeout: int = 0):
        self.calls.append({"url": url, "data": data, "json": json, "timeout": timeout})
        return self.response


class FakeRetrySession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request_with_retry(self, method: str, url: str, data=None, timeout: int = 0):
        self.calls.append({"method": method, "url": url, "data": data, "timeout": timeout})
        return self.response


class SourceSearchScraperTests(unittest.TestCase):
    def test_build_brief_grid_payload_matches_real_brief_grid_request_shape(self):
        payload = build_brief_grid_payload(
            source_journal="北京大学教育评论",
            classid="WD0FTY92",
            page_num=1,
            page_size=20,
        )

        query_json = json.loads(payload["QueryJson"])

        self.assertEqual(payload["pageNum"], 1)
        self.assertEqual(payload["pageSize"], 20)
        self.assertEqual(payload["sortField"], "")
        self.assertEqual(payload["sortType"], "")
        self.assertEqual(payload["aside"], "(文献来源：北京大学教育评论)")
        self.assertEqual(payload["searchFrom"], "资源范围：总库")
        self.assertEqual(query_json["Platform"], "")
        self.assertEqual(query_json["Products"], "")
        self.assertEqual(query_json["ExScope"], 1)
        self.assertEqual(query_json["SearchType"], 2)
        self.assertEqual(query_json["Rlang"], "CHINESE")
        self.assertEqual(query_json["View"], "changeDBCh")
        self.assertEqual(query_json["SearchFrom"], 1)
        self.assertEqual(query_json["QNode"]["QGroup"][0]["Items"][0]["Field"], "LY")
        self.assertEqual(query_json["QNode"]["QGroup"][0]["Items"][0]["Value"], "北京大学教育评论")
        self.assertEqual(query_json["QNode"]["QGroup"][0]["Items"][0]["Operator"], "FUZZY")

    def test_build_brief_grid_payload_accepts_operator_override(self):
        payload = build_brief_grid_payload(
            source_journal="系统工程",
            classid="WD0FTY92",
            operator="0",
        )

        item = json.loads(payload["QueryJson"])["QNode"]["QGroup"][0]["Items"][0]

        self.assertEqual(item["Field"], "LY")
        self.assertEqual(item["Value"], "系统工程")
        self.assertEqual(item["Operator"], "0")

    def test_build_brief_grid_payload_can_add_year_range(self):
        payload = build_brief_grid_payload(
            source_journal="北京大学教育评论",
            classid="WD0FTY92",
            page_num=1,
            page_size=20,
            year="2025",
        )

        query_json = json.loads(payload["QueryJson"])
        groups = query_json["QNode"]["QGroup"]
        date_item = groups[1]["Items"][0]

        self.assertEqual(groups[0]["Items"][0]["Field"], "LY")
        self.assertEqual(groups[1]["Key"], "ControlGroup")
        self.assertEqual(date_item["Key"], "span[value=PT]")
        self.assertEqual(date_item["Field"], "PT")
        self.assertEqual(date_item["Operator"], 7)
        self.assertEqual(date_item["Value"], "2025-01-01")
        self.assertEqual(date_item["Value2"], "2025-12-31")

    def test_build_brief_grid_payload_uses_real_month_boundaries(self):
        payload = build_brief_grid_payload(
            source_journal="北京大学教育评论",
            classid="WD0FTY92",
            year="2024-02",
        )

        date_item = json.loads(payload["QueryJson"])["QNode"]["QGroup"][1]["Items"][0]

        self.assertEqual(date_item["Value"], "2024-02-01")
        self.assertEqual(date_item["Value2"], "2024-02-29")

    def test_parse_brief_grid_response_extracts_rows_and_total_count(self):
        html = """
        <div id="briefBox">
          <div class="toolbar">
            <div id="countPageDiv" class="result-con-r">
              <span class="pagerTitleCell"><em>1,523</em></span>
            </div>
          </div>
          <table class="result-table-list">
            <tbody>
              <tr>
                <td class="name"><a class="fz14" href="https://example.com/detail">文章A</a></td>
                <td class="author"><a>张三</a>;<a>李四</a></td>
                <td class="source"><p><a>北京大学教育评论</a></p></td>
                <td class="date">2026-04-10</td>
                <td class="data"><span>期刊</span></td>
                <td class="quote"><a href="javascript:void(0);">12</a></td>
                <td class="download"><div><a class="downloadCnt" href="javascript:void(0);">140</a></div></td>
                <td class="operat"><a class="icon-collect" data-dbname="CJFQ" data-resource="JOURNAL" data-filename="BJPL202602001"></a></td>
              </tr>
            </tbody>
          </table>
        </div>
        """

        results, total = parse_brief_grid_response(html, "北京大学教育评论")

        self.assertEqual(total, 1523)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "文章A")
        self.assertEqual(results[0]["authors"], "张三;李四")
        self.assertEqual(results[0]["journal"], "北京大学教育评论")
        self.assertEqual(results[0]["publicationTime"], "2026-04-10")
        self.assertEqual(results[0]["quote_frequency"], "12")
        self.assertEqual(results[0]["download_count"], "140")
        self.assertEqual(results[0]["file_name"], "BJPL202602001")
        self.assertEqual(results[0]["db_name"], "CJFQ")
        self.assertEqual(results[0]["db_type"], "JOURNAL")
        self.assertEqual(results[0]["detail_url"], "https://example.com/detail")

    def test_parse_brief_grid_response_rejects_unrecognized_empty_message(self):
        html = '<div id="briefBox"><p class="no-content">登录状态异常</p></div>'

        with self.assertRaisesRegex(RuntimeError, "结构异常"):
            parse_brief_grid_response(html, "北京大学教育评论")

        html = '<html><body><div class="no-content">login error</div></body></html>'

        with self.assertRaisesRegex(RuntimeError, "结构异常"):
            parse_brief_grid_response(html, "北京大学教育评论")

        with self.assertRaisesRegex(RuntimeError, "空响应"):
            parse_brief_grid_response("", "北京大学教育评论")

    def test_parse_brief_grid_response_rejects_malformed_success_response(self):
        with self.assertRaisesRegex(RuntimeError, "结构异常"):
            parse_brief_grid_response("<html><body>temporary</body></html>", "北京大学教育评论")

    def test_build_brief_grid_payload_accepts_explicit_date_range(self):
        payload = build_brief_grid_payload(
            source_journal="北京大学教育评论",
            classid="WD0FTY92",
            date_start="2016-01-01",
            date_end="2026-12-31",
        )

        date_item = json.loads(payload["QueryJson"])["QNode"]["QGroup"][1]["Items"][0]
        self.assertEqual(date_item["Value"], "2016-01-01")
        self.assertEqual(date_item["Value2"], "2026-12-31")

    def test_parse_brief_grid_response_returns_empty_when_no_content(self):
        html = '<div id="briefBox"><p class="no-content">抱歉，没有检索到数据</p></div>'

        results, total = parse_brief_grid_response(html, "北京大学教育评论")

        self.assertEqual(results, [])
        self.assertEqual(total, 0)

    def test_fetch_search_page_posts_form_data_and_parses_html_results(self):
        html = """
        <div id="briefBox">
          <div class="toolbar"><div id="countPageDiv" class="result-con-r"><span class="pagerTitleCell"><em>3</em></span></div></div>
          <table class="result-table-list"><tbody>
            <tr>
              <td class="name"><a class="fz14">文章A</a></td>
              <td class="author"><a>张三</a></td>
              <td class="source"><p><a>北京大学教育评论</a></p></td>
              <td class="date">2026-04-10</td>
              <td class="data"><span>期刊</span></td>
              <td class="quote"><a>5</a></td>
              <td class="download"><div><a class="downloadCnt">8</a></div></td>
              <td class="operat"><a class="icon-collect" data-dbname="CJFQ" data-resource="JOURNAL" data-filename="BJPL202602001"></a></td>
            </tr>
          </tbody></table>
        </div>
        """
        session = FakeSession(FakeResponse(html))

        results, total = fetch_search_page(
            session=session,
            api_url="https://kns.cnki.net/kns8s/brief/grid",
            source_journal="北京大学教育评论",
            page=1,
            page_size=20,
        )

        self.assertEqual(total, 3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "文章A")
        self.assertIsNotNone(session.calls[0]["data"])
        self.assertIsNone(session.calls[0]["json"])

    def test_fetch_search_page_uses_request_with_retry_when_available(self):
        html = """
        <div id="briefBox">
          <div class="toolbar"><div id="countPageDiv" class="result-con-r"><span class="pagerTitleCell"><em>0</em></span></div></div>
        </div>
        """
        session = FakeRetrySession(FakeResponse(html))

        fetch_search_page(
            session=session,
            api_url="https://kns.cnki.net/kns8s/brief/grid",
            source_journal="北京大学教育评论",
            page=1,
            page_size=20,
        )

        self.assertEqual(session.calls[0]["method"], "POST")
        self.assertEqual(session.calls[0]["url"], "https://kns.cnki.net/kns8s/brief/grid")
        self.assertIsNotNone(session.calls[0]["data"])


if __name__ == "__main__":
    unittest.main()
