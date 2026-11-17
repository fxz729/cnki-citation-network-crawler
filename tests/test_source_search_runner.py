import json
import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

from scrapers.base_scraper import BaseScraper
from scrapers.source_search_runner import (
    CnkiCookieReplacementRequired,
    CnkiVerificationRequired,
    PartialCitationRelationError,
    SourceSearchScraper,
    parse_cookie_header,
)


class SourceSearchRunnerTests(unittest.TestCase):
    def test_management_journal_uses_verified_exact_source_override(self):
        with tempfile.TemporaryDirectory() as td:
            scraper = SourceSearchScraper(output_dir=Path(td)/'out', cache_dir=Path(td)/'cache', cookie='x=y', env_path=Path(td)/'missing')
            self.assertEqual(scraper._journal_operator('管理学刊'), '0')
            self.assertEqual(scraper._journal_operator('未配置期刊'), 'FUZZY')

    def test_successful_citation_page_is_requested_once(self):
        with tempfile.TemporaryDirectory() as td:
            scraper = SourceSearchScraper(output_dir=Path(td)/"out",cache_dir=Path(td)/"cache",cookie="x=y",env_path=Path(td)/"missing")
            response = self._build_response({"code":0,"data":{"total":0,"data":[]}},200)
            with patch.object(scraper,"request_with_retry",return_value=response) as request:
                scraper.fetch_citation_list({"detail_context":{"api_host":"https://kns.cnki.net/restapi","kcms_link":"v=test"}},"citations","CJFDREF")
            self.assertEqual(request.call_count,1)

    def test_init_preserves_json_cookie_and_following_session_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text(
                'CNKI_COOKIE_SHARED_1=Ecp_LoginStuts={"IsAutoLogin":true}; LID=personal; SID_kns_new=session; SID_restapi=rest\n',
                encoding="utf-8",
            )
            with patch("scrapers.source_search_runner.load_cookie_from_env", return_value=""):
                scraper = SourceSearchScraper(
                    output_dir=base_dir / "output",
                    cache_dir=base_dir / "cache",
                    env_path=env_path,
                    shard_id=1,
                )

        self.assertEqual(scraper.session.cookies.get("Ecp_LoginStuts"), '{"IsAutoLogin":true}')
        self.assertEqual(scraper.session.cookies.get("LID"), "personal")
        self.assertEqual(scraper.session.cookies.get("SID_kns_new"), "session")
        self.assertEqual(scraper.session.cookies.get("SID_restapi"), "rest")

    def test_parse_cookie_header_preserves_semicolons_inside_quoted_values(self):
        cookie = 'session="first;second"; SID_kns_new=session'

        self.assertEqual(
            parse_cookie_header(cookie),
            {"session": "first;second", "SID_kns_new": "session"},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            cookie = 'Ecp_LoginStuts="{\\"IsAutoLogin\\":true}"; SID_kns_new=session'
            with patch("scrapers.source_search_runner.load_cookie_from_env", return_value=cookie):
                scraper = SourceSearchScraper(
                    output_dir=base_dir / "output",
                    cache_dir=base_dir / "cache",
                    env_path=base_dir / ".env",
                )

        self.assertEqual(scraper.session.cookies.get("Ecp_LoginStuts"), '{"IsAutoLogin":true}')
        self.assertEqual(scraper.session.cookies.get("SID_kns_new"), "session")

    def test_ensure_session_accepts_same_named_sid_cookies_from_multiple_domains(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="SID_kns_new=from-config",
                env_path=base_dir / ".env",
            )
            scraper.session.cookies.set(
                "SID_kns_new",
                "from-server",
                domain="kns.cnki.net",
                path="/",
            )

            with patch.object(scraper, "request_with_retry") as request:
                scraper.ensure_session()

        request.assert_not_called()

    def test_reload_shard_cookie_uses_environment_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=base_dir / "missing.env",
                shard_id=2,
            )
            with patch("scrapers.source_search_runner.load_cookie_from_env", return_value="fresh_cookie=new-value"):
                reloaded = scraper.reload_shard_cookie()

        self.assertTrue(reloaded)
        self.assertEqual(scraper.session.cookies.get_dict(), {"fresh_cookie": "new-value"})

    def test_reload_shard_cookie_rebuilds_complete_cookie_jar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text(
                'CNKI_COOKIE_SHARED_2=Ecp_LoginStuts={"IsAutoLogin":true}; LID=fresh-lid; SID_kns_new=fresh-session; SID_restapi=fresh-rest\n',
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=env_path,
                shard_id=2,
            )
            session = scraper.session

            reloaded = scraper.reload_shard_cookie()

        self.assertTrue(reloaded)
        self.assertIs(scraper.session, session)
        self.assertEqual(
            scraper.session.cookies.get_dict(),
            {
                "Ecp_LoginStuts": '{"IsAutoLogin":true}',
                "LID": "fresh-lid",
                "SID_kns_new": "fresh-session",
                "SID_restapi": "fresh-rest",
            },
        )

    def test_request_reloads_shard_cookie_after_first_401(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_2=fresh_cookie=new-value\n", encoding="utf-8")
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=env_path,
                shard_id=2,
            )
            unauthorized = Mock(status_code=401, text="unauthorized", url="https://kns.cnki.net/restapi/citation-api/v1/literature/citations?v=secret")
            ok = Mock(status_code=200, text="ok", url=unauthorized.url)

            with patch.object(BaseScraper, "request_with_retry", side_effect=[unauthorized, ok]) as request:
                response = scraper.request_with_retry("GET", unauthorized.url, timeout=15)

        self.assertIs(response, ok)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(scraper.session.cookies.get_dict(), {"fresh_cookie": "new-value"})

    def test_second_401_requires_manual_cookie_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_3=fresh_cookie=new-value\n", encoding="utf-8")
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=env_path,
                shard_id=3,
            )
            unauthorized = Mock(status_code=401, text="unauthorized", url="https://kns.cnki.net/kns8s/brief/grid")

            with patch.object(BaseScraper, "request_with_retry", side_effect=[unauthorized, unauthorized]) as request:
                with self.assertRaisesRegex(RuntimeError, "人工更新 Cookie"):
                    scraper.request_with_retry("GET", unauthorized.url)

        self.assertEqual(request.call_count, 2)

    def test_missing_reloaded_cookie_returns_without_cooldown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_2=\n", encoding="utf-8")
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=env_path,
                shard_id=2,
            )
            unauthorized = Mock(status_code=401, text="unauthorized", url="https://kns.cnki.net/restapi/citation-api/v1/literature/citations")

            with patch.object(BaseScraper, "request_with_retry", return_value=unauthorized) as request:
                response = scraper.request_with_retry("GET", unauthorized.url)

        self.assertIs(response, unauthorized)
        request.assert_called_once()
        self.assertEqual(scraper.session.cookies.get_dict(), {"stale_cookie": "old-value"})

    def test_missing_reloaded_cookie_does_not_require_manual_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_2=\n", encoding="utf-8")
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=env_path,
                shard_id=2,
            )
            unauthorized = Mock(status_code=401, text="unauthorized", url="https://kns.cnki.net/kns8s/brief/grid")

            with patch.object(BaseScraper, "request_with_retry", return_value=unauthorized) as request:
                response = scraper.request_with_retry("POST", unauthorized.url)

        self.assertIs(response, unauthorized)
        request.assert_called_once()

    def test_abnormal_response_log_has_shard_endpoint_status_and_truncated_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="secret_cookie=must-not-be-logged",
                env_path=base_dir / ".env",
                shard_id=3,
                max_retries=0,
            )
            response = Mock(
                status_code=403,
                text="SID_restapi=secret-rest token=secret-token Cookie: secret-cookie Error: " + "x" * 700,
                url="https://kns.cnki.net/restapi/citation-api/v1/literature/citations?v=secret-token",
            )

            with patch.object(scraper.session, "request", return_value=response), patch.object(
                scraper, "sleep_between_requests"
            ), self.assertLogs(scraper.logger, level="WARNING") as logs:
                with self.assertRaisesRegex(ValueError, "403"):
                    scraper.request_with_retry("GET", response.url)

        log = "\n".join(logs.output)
        self.assertIn("timestamp=", log)
        self.assertIn("shard=3", log)
        self.assertIn("status=403", log)
        self.assertIn("endpoint=citation-list", log)
        self.assertIn("SID_restapi=[REDACTED]", log)
        self.assertIn("token=[REDACTED]", log)
        self.assertIn("Cookie:[REDACTED]", log)
        self.assertIn("x" * 400, log)
        self.assertNotIn("x" * 501, log)
        self.assertNotIn("secret-token", log)
        self.assertNotIn("secret-rest", log)
        self.assertNotIn("secret-cookie", log)
        self.assertNotIn("must-not-be-logged", log)

    def test_abnormal_response_log_redacts_complete_cookie_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            response = Mock(
                status_code=403,
                text="Cookie: SID_kns_new=secret; LID=private; foo=bar Error: denied",
                url="https://kns.cnki.net/restapi/citation-api/v1/literature/citations",
            )

            with self.assertLogs(scraper.logger, level="WARNING") as logs:
                scraper.observe_response(response, response.url)

        log = "\n".join(logs.output)
        self.assertIn("Cookie:[REDACTED]", log)
        self.assertNotIn("secret", log)
        self.assertNotIn("private", log)
        self.assertNotIn("foo=bar", log)
        self.assertIn("Error: denied", log)

    def test_request_forces_redirects_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            response = Mock(status_code=200, text="ok", url="https://kns.cnki.net/detail")

            with patch.object(BaseScraper, "request_with_retry", return_value=response) as request:
                scraper.request_with_retry(
                    "GET",
                    "https://kns.cnki.net/detail",
                    allow_redirects=True,
                )

        self.assertFalse(request.call_args.kwargs["allow_redirects"])

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            unauthorized = Mock(
                status_code=401,
                text='{"message":"请求已过期！"}',
                url="https://kns.cnki.net/restapi/literature-api/v1/articles/detail?vv=expired",
            )

            with patch.object(BaseScraper, "request_with_retry", return_value=unauthorized) as request, patch.object(
                scraper, "reload_shard_cookie"
            ) as reload_cookie:
                response = scraper.request_with_retry("POST", unauthorized.url)

        self.assertIs(response, unauthorized)
        request.assert_called_once()
        reload_cookie.assert_not_called()

    def test_expired_reader_detail_is_disabled_and_existing_vid_still_fetches_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            unauthorized = self._build_response({"code": 400, "message": "请求已过期！"}, 401)
            references = self._build_relation_payloads([("参考文献A", "A1")], total=1)
            ok = self._build_response(references, 200)
            detail_requests = 0

            def fake_request(method, url, **kwargs):
                nonlocal detail_requests
                if "/articles/detail" in url:
                    detail_requests += 1
                    return unauthorized
                if "/original-references-auth" in url:
                    return ok
                raise AssertionError(f"unexpected request: {url}")

            articles = [
                {
                    "detail_context": {
                        "api_host": "https://kns.cnki.net/restapi",
                        "kcms_link": f"v=reader-token-{idx}&uniplatform=NZKPT",
                        "dbname": "CJFDLAST2024",
                    }
                }
                for idx in (1, 2)
            ]
            with patch.object(scraper, "request_with_retry", side_effect=fake_request), patch.object(
                scraper, "fetch_citation_list", side_effect=AssertionError("should use reader references")
            ):
                first = scraper.fetch_citation_relation_items(articles[0], "references", "CJFDREF", max_pages=0)
                second = scraper.fetch_citation_relation_items(articles[1], "references", "CJFDREF", max_pages=0)

        self.assertEqual([item["title"] for item in first], ["参考文献A"])
        self.assertEqual([item["title"] for item in second], ["参考文献A"])
        self.assertEqual(detail_requests, 1)

    def test_expired_reader_references_is_disabled_and_falls_back_without_refreshing_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            unauthorized = self._build_response({"code": 400, "message": "请求已过期！"}, 401)
            fallback = self._build_relation_payloads([("回退文献", "F1")], total=1)
            reader_requests = 0

            def fake_request(method, url, **kwargs):
                nonlocal reader_requests
                if "/original-references-auth" in url:
                    reader_requests += 1
                    return unauthorized
                raise AssertionError(f"unexpected request: {url}")

            articles = [
                {
                    "detail_context": {
                        "api_host": "https://kns.cnki.net/restapi",
                        "kcms_link": f"v=reader-token-{idx}&uniplatform=NZKPT",
                        "dbname": "CJFDLAST2024",
                    },
                    "reader_detail": {"metadata": [{"name": "vid", "value": f"reader-token-{idx}"}]},
                }
                for idx in (1, 2)
            ]
            with patch.object(scraper, "request_with_retry", side_effect=fake_request), patch.object(
                scraper, "fetch_citation_list", return_value=fallback
            ) as fetch_fallback, patch.object(
                scraper, "_refresh_reader_context", side_effect=AssertionError("should not refresh Cookie")
            ):
                first = scraper.fetch_citation_relation_items(articles[0], "references", "CJFDREF", max_pages=1)
                second = scraper.fetch_citation_relation_items(articles[1], "references", "CJFDREF", max_pages=1)

        self.assertEqual([item["title"] for item in first], ["回退文献"])
        self.assertEqual([item["title"] for item in second], ["回退文献"])
        self.assertEqual(reader_requests, 1)
        self.assertEqual(fetch_fallback.call_count, 2)

    def test_disabled_reader_detail_uses_context_vid_without_dbname(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            scraper.reader_detail_available = False
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=context-token&uniplatform=NZKPT",
                }
            }
            payload = self._build_relation_payloads([("参考文献A", "A1")], total=1)

            def fetch_reader_references(current_article, **kwargs):
                self.assertEqual(
                    current_article.get("reader_detail", {}).get("kcms_link"),
                    "v=context-token&uniplatform=NZKPT",
                )
                return payload

            with patch.object(scraper, "fetch_reader_detail", side_effect=AssertionError("reader detail is disabled")), patch.object(
                scraper, "fetch_reader_references", side_effect=fetch_reader_references
            ) as fetch_reader_references, patch.object(
                scraper, "fetch_citation_list", side_effect=AssertionError("should use context vid")
            ):
                items = scraper.fetch_citation_relation_items(article, "references", "CJFDREF", max_pages=0)

        self.assertEqual([item["title"] for item in items], ["参考文献A"])
        fetch_reader_references.assert_called_once()

    def test_abnormal_response_log_redacts_json_and_bearer_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            response = Mock(
                status_code=401,
                text='{"token":"json-secret","Authorization":"Bearer bearer-secret"}',
                url="https://example.com/detail",
            )

            with self.assertLogs(scraper.logger, level="WARNING") as logs:
                scraper.observe_response(response, response.url)

        log = "\n".join(logs.output)
        self.assertNotIn("json-secret", log)
        self.assertNotIn("bearer-secret", log)
        self.assertIn("[REDACTED]", log)

    def test_classify_endpoint_identifies_reader_detail_api(self):
        endpoint = SourceSearchScraper._classify_endpoint(
            "https://kns.cnki.net/restapi/literature-api/v1/articles/detail?vv=secret"
        )

        self.assertEqual(endpoint, "reader-detail")

    def test_search_journals_raises_clear_error_without_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scraper = SourceSearchScraper(
                output_dir=Path(temp_dir) / "output",
                cache_dir=Path(temp_dir) / "cache",
                cookie="",
                env_path=Path(temp_dir) / ".env",
            )

            with self.assertRaisesRegex(ValueError, "CNKI_COOKIE"):
                scraper.search_journals(["北京大学教育评论"], max_pages=1)

    def test_init_loads_cookie_from_env_file_when_cookie_not_passed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE=file-cookie\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                scraper = SourceSearchScraper(
                    output_dir=base_dir / "output",
                    cache_dir=base_dir / "cache",
                    cookie="",
                    env_path=env_path,
                )

        self.assertEqual(scraper.cookie, "file-cookie")

    def test_refreshed_session_cookie_replaces_initial_cookie_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="stale_cookie=old-value",
                env_path=base_dir / ".env",
            )
            scraper.session.cookies.clear()
            scraper.session.cookies.set("fresh_cookie", "new-value")

            prepared = scraper.session.prepare_request(
                __import__("requests").Request("GET", "https://kns.cnki.net/kns8s/defaultresult/index")
            )

        self.assertEqual(prepared.headers["Cookie"], "fresh_cookie=new-value")

    def test_refresh_session_preserves_login_cookie_and_replaces_session_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="login_cookie=authenticated; SID_kns_new=stale-session",
                env_path=base_dir / ".env",
            )

            def set_fresh_session(*args, **kwargs):
                scraper.session.cookies.set("SID_kns_new", "fresh-session")

            with patch.object(scraper, "request_with_retry", side_effect=set_fresh_session):
                scraper.refresh_session()

            prepared = scraper.session.prepare_request(
                __import__("requests").Request("GET", "https://kns.cnki.net/kns8s/defaultresult/index")
            )

        self.assertEqual(prepared.headers["Cookie"], "login_cookie=authenticated; SID_kns_new=fresh-session")

    def test_ensure_detail_context_refreshes_expired_search_token_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_url"] = "https://kns.cnki.net/kcms2/article/abstract?v=old"
            html = '<input id="paramkcmslink" value="v=fresh&amp;uniplatform=NZKPT">'

            with patch.object(
                scraper,
                "fetch_detail_page",
                side_effect=[CnkiVerificationRequired("expired"), html],
            ) as fetch_detail, patch.object(
                scraper,
                "refresh_article_search_token",
                return_value=True,
            ) as refresh_token:
                context = scraper.ensure_detail_context(article)

        self.assertEqual(fetch_detail.call_count, 2)
        refresh_token.assert_called_once_with(article)
        self.assertEqual(context["kcms_link"], "v=fresh&uniplatform=NZKPT")

    def test_refresh_article_search_token_matches_article_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["publicationTime"] = "2016-03-01"
            article["detail_url"] = "https://kns.cnki.net/old"
            refreshed = {**article, "detail_url": "https://kns.cnki.net/fresh"}

            with patch.object(scraper, "search_page", return_value=([refreshed], 1)) as search_page:
                result = scraper.refresh_article_search_token(article)

        self.assertTrue(result)
        self.assertEqual(article["detail_url"], "https://kns.cnki.net/fresh")
        self.assertEqual(search_page.call_args.kwargs["year"], "2016")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            response = Mock(
                status_code=302,
                headers={"Location": "https://kns.cnki.net/verify/home?captchaId=x"},
                text="",
                url="https://kns.cnki.net/detail",
            )
            with patch.object(BaseScraper, "request_with_retry", return_value=response):
                with self.assertRaises(CnkiVerificationRequired):
                    scraper.request_with_retry("GET", response.url)

    def test_run_keeps_verification_checkpoint_paused_until_recovery_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            output_dir.mkdir(parents=True)
            checkpoint_path.write_text(
                json.dumps({"blocked_reason": "verification_required"}),
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )
            with patch.object(scraper, "process_journal") as process_journal:
                scraper.run(["期刊A"], resume=True)

        self.assertEqual(scraper.blocked_reason, "verification_required")
        process_journal.assert_not_called()

    def test_run_keeps_verification_checkpoint_paused_without_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            output_dir.mkdir(parents=True)
            checkpoint_path.write_text(
                json.dumps({"blocked_reason": "verification_required"}),
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )
            with patch.object(scraper, "process_journal") as process_journal:
                scraper.run(["期刊A"], resume=False)

        self.assertEqual(scraper.blocked_reason, "verification_required")
        process_journal.assert_not_called()

    def test_run_records_verification_block_without_marking_articles_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            output_dir.mkdir(parents=True)
            checkpoint_path.write_text(
                json.dumps({"verification_probe_result": "ready"}),
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )
            with patch.object(
                scraper,
                "process_journal",
                side_effect=CnkiVerificationRequired("verify"),
            ):
                scraper.run(["期刊A"], resume=True)

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

        self.assertEqual(checkpoint["blocked_reason"], "verification_required")
        self.assertEqual(checkpoint["verification_probe_delay_minutes"], 15)
        from datetime import datetime
        delay = datetime.fromisoformat(checkpoint["verification_probe_next_at"]) - datetime.fromisoformat(checkpoint["updated_at"])
        self.assertAlmostEqual(delay.total_seconds(), 900, delta=2)
        self.assertEqual(checkpoint["verification_probe_result"], "")
        self.assertEqual(checkpoint["completed_journals"], [])
        self.assertFalse((output_dir / "enriched.jsonl").exists())

    def test_run_records_cookie_replacement_block_and_notifies_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )

            with patch.object(
                scraper,
                "process_journal",
                side_effect=CnkiCookieReplacementRequired("刷新 Cookie 后仍收到 401"),
            ), patch("scrapers.source_search_runner.send_cookie_replacement_email", return_value=True) as notify:
                scraper.run(["期刊A"], resume=True)
                scraper.run(["期刊A"], resume=True)

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            alert_key_exists = (base_dir / "cache" / ".cookie-alert-key").exists()

        self.assertEqual(checkpoint["blocked_reason"], "cookie_replacement_required")
        self.assertTrue(checkpoint["cookie_replacement_notified"])
        self.assertTrue(checkpoint["cookie_replacement_cookie_fingerprint"])
        self.assertNotEqual(
            checkpoint["cookie_replacement_cookie_fingerprint"],
            sha256(b"cookie=value").hexdigest(),
        )
        self.assertTrue(alert_key_exists)
        notify.assert_called_once()

    def test_run_new_process_does_not_renotify_same_cookie(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_1=same-cookie=value\n", encoding="utf-8")

            with patch("scrapers.source_search_runner.send_cookie_replacement_email", return_value=True) as notify:
                first_scraper = SourceSearchScraper(
                    output_dir=output_dir,
                    cache_dir=base_dir / "cache",
                    env_path=env_path,
                    checkpoint_path=checkpoint_path,
                )
                with patch.object(
                    first_scraper,
                    "process_journal",
                    side_effect=CnkiCookieReplacementRequired("刷新 Cookie 后仍收到 401"),
                ):
                    first_scraper.run(["期刊A"], resume=True)

                second_scraper = SourceSearchScraper(
                    output_dir=output_dir,
                    cache_dir=base_dir / "cache",
                    env_path=env_path,
                    checkpoint_path=checkpoint_path,
                )
                with patch.object(
                    second_scraper,
                    "process_journal",
                    side_effect=CnkiCookieReplacementRequired("刷新 Cookie 后仍收到 401"),
                ):
                    second_scraper.run(["期刊A"], resume=True)

        notify.assert_called_once()

    def test_run_notifies_again_after_cookie_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            env_path = base_dir / ".env"
            env_path.write_text("CNKI_COOKIE_SHARED_1=first-cookie=value\n", encoding="utf-8")
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                env_path=env_path,
                checkpoint_path=checkpoint_path,
            )

            with patch.object(
                scraper,
                "process_journal",
                side_effect=CnkiCookieReplacementRequired("刷新 Cookie 后仍收到 401"),
            ), patch("scrapers.source_search_runner.send_cookie_replacement_email", return_value=True) as notify:
                scraper.run(["期刊A"], resume=True)
                env_path.write_text("CNKI_COOKIE_SHARED_1=second-cookie=value\n", encoding="utf-8")
                scraper.reload_shard_cookie()
                scraper.blocked_reason = ""
                scraper.run(["期刊A"], resume=True)

        self.assertEqual(notify.call_count, 2)

    def test_run_does_not_notify_for_verification_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            with patch.object(
                scraper,
                "process_journal",
                side_effect=CnkiVerificationRequired("verify"),
            ), patch("scrapers.source_search_runner.send_cookie_replacement_email") as notify:
                scraper.run(["期刊A"], resume=True)

        notify.assert_not_called()

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="SID_kns_new=secret",
                env_path=base_dir / ".env",
            )

            with patch.object(scraper.session, "request") as request:
                with self.assertRaisesRegex(ValueError, "非 CNKI"):
                    scraper.request_with_retry("GET", "https://attacker.example/detail")

        request.assert_not_called()

    def test_request_rejects_non_standard_cnki_port(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="SID_kns_new=secret",
                env_path=base_dir / ".env",
            )

            with self.assertRaisesRegex(ValueError, "非 CNKI"):
                scraper.request_with_retry("GET", "https://kns.cnki.net:8443/detail")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="SID_kns_new=secret",
                env_path=base_dir / ".env",
            )

            cnki = scraper.session.prepare_request(
                __import__("requests").Request("GET", "https://kns.cnki.net/kns8s/defaultresult/index")
            )
            external = scraper.session.prepare_request(
                __import__("requests").Request("GET", "https://attacker.example/detail")
            )

        self.assertIn("SID_kns_new=secret", cnki.headers.get("Cookie", ""))
        self.assertNotIn("Cookie", external.headers)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value; SID_kns_new=session",
                env_path=base_dir / ".env",
                max_retries=0,
            )
            response = self._build_response('<div id="countPageDiv"><span class="pagerTitleCell"><em>0</em></span></div>', 200)

            with patch.object(scraper, "request_with_retry", return_value=response) as request:
                scraper.search_page("北京大学教育评论", page=1, page_size=100, year="2026")

        self.assertEqual(request.call_args.kwargs["data"]["pageSize"], 50)

    def test_search_journals_writes_raw_results_for_each_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            page_results = [self._build_article("文章A", "A1")]

            with patch.object(scraper, "search_page", return_value=(page_results, 1)), patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(["北京大学教育评论"], max_pages=1)

            raw_output = (base_dir / "output" / "raw.jsonl").read_text(encoding="utf-8").strip().splitlines()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(raw_output), 1)
        self.assertIn("文章A", raw_output[0])

    def test_search_journals_auto_pages_until_total_when_max_pages_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            first_page = [self._build_article("文章A", "A1"), self._build_article("文章B", "B1")]
            second_page = [self._build_article("文章C", "C1")]

            with patch.object(scraper, "search_page", side_effect=[(first_page, 3), (second_page, 3)]) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(["北京大学教育评论"], page_size=2, max_pages=0)

        self.assertEqual([article["title"] for article in results], ["文章A", "文章B", "文章C"])
        self.assertEqual([call.kwargs["page"] for call in search_page.call_args_list], [1, 2])

    def test_search_journals_can_use_year_slices_without_turning_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article_a = self._build_article("文章A", "A1")
            article_b = self._build_article("文章B", "B1")

            with patch.object(scraper, "search_page", side_effect=[([article_a], 1), ([article_b], 1)]) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    page_size=20,
                    max_pages=0,
                    year_slices=["2025", "2024"],
                )

        self.assertEqual([article["title"] for article in results], ["文章A", "文章B"])
        self.assertEqual([call.kwargs["page"] for call in search_page.call_args_list], [1, 1])
        self.assertEqual([call.kwargs["year"] for call in search_page.call_args_list], ["2025", "2024"])

    def test_search_journals_splits_large_year_slice_by_adaptive_date_ranges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            year_preview = [self._build_article(f"年预览{index}", f"Y{index}") for index in range(50)]
            first_half = [self._build_article(f"上半年文章{index}", f"L{index}") for index in range(40)]
            second_half = [self._build_article(f"下半年文章{index}", f"R{index}") for index in range(40)]

            with patch.object(
                scraper,
                "search_page",
                side_effect=[(year_preview, 80), (first_half, 40), (second_half, 40)],
            ) as search_page, patch.object(scraper, "sleep_between_requests"):
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    page_size=100,
                    max_pages=0,
                    year_slices=["2025"],
                )

        self.assertEqual(len(results), 80)
        self.assertEqual(results[0]["title"], "上半年文章0")
        self.assertEqual(results[-1]["title"], "下半年文章39")
        self.assertEqual(len(search_page.call_args_list), 3)
        self.assertEqual(search_page.call_args_list[0].kwargs["year"], "2025")
        self.assertEqual(search_page.call_args_list[1].kwargs["year"], "2025-01-01..2025-07-02")
        self.assertEqual(search_page.call_args_list[2].kwargs["year"], "2025-07-03..2025-12-31")
        self.assertTrue(all(call.kwargs["page"] == 1 for call in search_page.call_args_list))

    def test_single_day_slice_takes_first_page_and_records_truncation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            pages = [
                ([self._build_article("A", "A1"), self._build_article("B", "B1")], 5),
            ]

            with patch.object(scraper, "search_page", side_effect=pages) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(
                    ["Journal"],
                    page_size=2,
                    max_pages=0,
                    year_slices=["2025-01-20..2025-01-20"],
                )

            truncation_path = base_dir / "output" / "truncated_slices.jsonl"
            records = [
                json.loads(line)
                for line in truncation_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual([article["title"] for article in results], ["A", "B"])
        self.assertEqual([call.kwargs["page"] for call in search_page.call_args_list], [1])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], "single-day-overflow")
        self.assertEqual(records[0]["declared_total"], 5)
        self.assertEqual(records[0]["taken"], 2)

    def test_journal_source_match_rejects_fuzzy_collisions(self):
        from scrapers.source_search_runner import journal_source_matches

        self.assertTrue(journal_source_matches("系统工程", "系统工程"))
        self.assertTrue(journal_source_matches("系统 工程", "系统工程"))
        self.assertFalse(journal_source_matches("系统工程 与电子技术", "系统工程"))
        self.assertFalse(journal_source_matches("信息 系统工程", "系统工程"))
        self.assertFalse(journal_source_matches("经济 管理学刊", "管理学刊"))
        self.assertFalse(journal_source_matches("广州 公共管理评论", "公共管理评论"))
        # 来源缺失时保留，避免误删真实数据
        self.assertTrue(journal_source_matches("", "管理学刊"))

    def test_non_convergent_slice_stops_bisecting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            articles = [self._build_article("A", "A1"), self._build_article("B", "B1")]
            pages = [
                (articles, 52),
                (articles, 52),
            ]

            with patch.object(scraper, "search_page", side_effect=pages) as search_page:
                slices = list(scraper._iter_adaptive_date_slices("Journal", "2025", 50))

            truncation_path = base_dir / "output" / "truncated_slices.jsonl"
            records = [
                json.loads(line)
                for line in truncation_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][0], "2025")
        self.assertEqual([article["title"] for article in slices[0][1]], ["A", "B"])
        self.assertEqual(
            [call.kwargs["year"] for call in search_page.call_args_list],
            ["2025", "2025-01-01..2025-07-02"],
        )
        self.assertEqual(records[0]["reason"], "non-convergent")

    def test_source_filtered_slice_uses_actual_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            articles = [self._build_article("A", "A1"), self._build_article("B", "B1")]
            with patch.object(scraper, "search_page", side_effect=[(articles, 48)]):
                slices = list(scraper._iter_adaptive_date_slices("Journal", "2025", 50))
            records = [
                json.loads(line)
                for line in (base_dir / "output" / "truncated_slices.jsonl")
                .read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][2], 2)
        self.assertEqual([a["title"] for a in slices[0][1]], ["A", "B"])
        self.assertEqual(records[0]["reason"], "source-filtered")
        self.assertEqual(records[0]["declared_total"], 48)

    def test_fully_filtered_slice_yields_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            with patch.object(scraper, "search_page", side_effect=[([], 5)]):
                slices = list(scraper._iter_adaptive_date_slices("Journal", "2025", 50))

        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][1], [])
        self.assertEqual(slices[0][2], 0)

    def test_journal_override_switches_source_operator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = self._build_override_scraper(base_dir)
            scraper.session.cookies.set("SID_kns_new", "sid")
            html = '<div id="briefBox"><p class="no-content">抱歉，没有检索到数据</p></div>'

            with patch.object(
                scraper, "request_with_retry", return_value=self._build_response(html, 200)
            ) as request:
                scraper.search_page("系统工程", page=1, page_size=50, year="2025")
                overridden = json.loads(request.call_args.kwargs["data"]["QueryJson"])
                operator = overridden["QNode"]["QGroup"][0]["Items"][0]["Operator"]

            with patch.object(
                scraper, "request_with_retry", return_value=self._build_response(html, 200)
            ) as other:
                scraper.search_page("苏区研究", page=1, page_size=50, year="2025")
                plain = json.loads(other.call_args.kwargs["data"]["QueryJson"])
                plain_operator = plain["QNode"]["QGroup"][0]["Items"][0]["Operator"]

        self.assertEqual(operator, "0")
        self.assertEqual(plain_operator, "FUZZY")

    def test_monthly_override_expands_year_slice_into_months(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = self._build_override_scraper(base_dir)

            expanded = scraper._expand_override_slices("系统工程", ["2025"])
            untouched = scraper._expand_override_slices("苏区研究", ["2025"])

        self.assertEqual(len(expanded), 12)
        self.assertEqual(expanded[0], "2025-01-01..2025-01-31")
        self.assertEqual(expanded[-1], "2025-12-01..2025-12-31")
        self.assertEqual(untouched, ["2025"])

    def test_monthly_override_takes_first_page_without_bisecting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = self._build_override_scraper(base_dir)
            articles = [self._build_article("A", "A1"), self._build_article("B", "B1")]

            with patch.object(scraper, "search_page", side_effect=[(articles, 48)]) as search_page:
                slices = list(
                    scraper._iter_adaptive_date_slices("系统工程", "2025-01-01..2025-01-31", 50)
                )

            records = [
                json.loads(line)
                for line in (base_dir / "output" / "truncated_slices.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]

        self.assertEqual(len(search_page.call_args_list), 1)
        self.assertEqual([call.kwargs["page"] for call in search_page.call_args_list], [1])
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][0], "2025-01-01..2025-01-31")
        self.assertEqual(slices[0][2], 2)
        self.assertEqual(records[0]["reason"], "source-filtered")

    def test_monthly_override_records_overflow_when_page_is_full(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = self._build_override_scraper(base_dir)
            articles = [self._build_article(f"A{index}", f"A{index}") for index in range(50)]

            with patch.object(scraper, "search_page", side_effect=[(articles, 120)]) as search_page:
                slices = list(
                    scraper._iter_adaptive_date_slices("系统工程", "2025-01-01..2025-01-31", 50)
                )

            records = [
                json.loads(line)
                for line in (base_dir / "output" / "truncated_slices.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]

        self.assertEqual(len(search_page.call_args_list), 1)
        self.assertEqual(len(slices), 1)
        self.assertEqual(records[0]["reason"], "fixed-slice-overflow")
        self.assertEqual(records[0]["declared_total"], 120)
        self.assertEqual(records[0]["taken"], 50)

    def test_override_does_not_change_unlisted_journal_slicing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = self._build_override_scraper(base_dir)
            year_preview = [self._build_article(f"年预览{index}", f"Y{index}") for index in range(50)]
            first_half = [self._build_article(f"上半年文章{index}", f"L{index}") for index in range(40)]
            second_half = [self._build_article(f"下半年文章{index}", f"R{index}") for index in range(40)]

            with patch.object(
                scraper,
                "search_page",
                side_effect=[(year_preview, 80), (first_half, 40), (second_half, 40)],
            ) as search_page, patch.object(scraper, "sleep_between_requests"):
                results = scraper.search_journals(
                    ["苏区研究"], page_size=100, max_pages=0, year_slices=["2025"]
                )

        self.assertEqual(len(results), 80)
        self.assertEqual(len(search_page.call_args_list), 3)

    def test_adaptive_slices_reject_zero_total_with_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")

            with patch.object(scraper, "search_page", return_value=([article], 0)):
                with self.assertRaisesRegex(RuntimeError, "总数为 0"):
                    scraper.search_journals(
                        ["北京大学教育评论"],
                        page_size=50,
                        year_slices=["2025"],
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            preview = [self._build_article(f"预览{index}", f"P{index}") for index in range(50)]
            left = [self._build_article(f"左{index}", f"L{index}") for index in range(40)]
            right = [self._build_article(f"右{index}", f"R{index}") for index in range(39)]
            right.append(self._build_article("重复", "L0"))

            with patch.object(scraper, "search_page", side_effect=[(preview, 80), (left, 40), (right, 40)]):
                with self.assertRaisesRegex(RuntimeError, "叶区间存在重复文章"):
                    scraper.search_journals(
                        ["北京大学教育评论"],
                        page_size=50,
                        max_pages=0,
                        year_slices=["2025"],
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("年文章", "Y1")

            with patch.object(scraper, "search_page", return_value=([article], 1)) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    page_size=50,
                    max_pages=0,
                    auto_detect_years=True,
                    year_end=2025,
                    year_start_floor=2025,
                )

        self.assertEqual([article["title"] for article in results], ["年文章"])
        self.assertEqual(search_page.call_count, 1)
        self.assertEqual(search_page.call_args.kwargs["year"], "2025-01-01..2025-12-31")

    def test_auto_detect_accepts_explicit_empty_full_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )

            with patch.object(scraper, "search_page", return_value=([], 0)) as search_page:
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    auto_detect_years=True,
                    year_end=2026,
                    year_start_floor=2025,
                )

        self.assertEqual(results, [])
        self.assertEqual(search_page.call_count, 1)
        self.assertEqual(search_page.call_args.kwargs["year"], "2025-01-01..2026-12-31")

    def test_search_journals_auto_detects_full_2016_to_2026_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            articles = [
                self._build_article("2026文章", "F2026"),
                self._build_article("2016文章", "F2016"),
            ]

            with patch.object(scraper, "search_page", return_value=(articles, 2)) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    page_size=50,
                    max_pages=0,
                    auto_detect_years=True,
                    year_end=2026,
                    year_start_floor=2016,
                )

        self.assertEqual([article["title"] for article in results], ["2026文章", "2016文章"])
        self.assertEqual(search_page.call_count, 1)
        self.assertEqual(search_page.call_args.kwargs["year"], "2016-01-01..2026-12-31")

    def test_search_journals_auto_detect_uses_year_start_floor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article_2026 = self._build_article("2026文章", "F2026")

            with patch.object(scraper, "search_page", return_value=([article_2026], 1)) as search_page, patch.object(
                scraper, "sleep_between_requests"
            ):
                results = scraper.search_journals(
                    ["北京大学教育评论"],
                    page_size=50,
                    max_pages=0,
                    auto_detect_years=True,
                    year_end=2026,
                    year_start_floor=2022,
                )

        self.assertEqual([article["title"] for article in results], ["2026文章"])
        self.assertEqual(search_page.call_count, 1)
        self.assertEqual(search_page.call_args.kwargs["year"], "2022-01-01..2026-12-31")

    def test_network_methods_use_request_with_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            scraper.session.cookies.set("SID_kns_new", "sid")
            article = self._build_article("文章A", "A1")
            article["detail_url"] = "https://kns.cnki.net/detail"
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            html_response = self._build_response("<html></html>", 200)
            metrics_response = self._build_response({"code": 0, "data": {"references": 0}}, 200)
            list_response = self._build_response({"code": 0, "data": {"total": 0, "data": []}}, 200)

            with patch.object(scraper, "request_with_retry", side_effect=[html_response, metrics_response, list_response]) as request:
                scraper.fetch_detail_page(article)
                scraper.fetch_citation_metrics(article)
                scraper.fetch_citation_list(article, relation_type="references", file_type="CJFDREF")

        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET", "GET", "GET"])
        self.assertEqual(request.call_args_list[0].args[1], "https://kns.cnki.net/detail")
        self.assertIn("quotations/metrics", request.call_args_list[1].args[1])
        self.assertIn("literature/references", request.call_args_list[2].args[1])

    def test_citation_dedup_preserves_same_title_distinct_files_without_database(self):
        from scrapers.source_search_runner import deduplicate_relation_items
        a={'title':'同名成果','file_name':'X1','db_name':'','year':'2020'}
        b={'title':'同名成果','file_name':'X2','db_name':'','year':'2022'}
        self.assertEqual(deduplicate_relation_items([a,b,dict(a)]),[a,b])

    def test_excess_distinct_citations_do_not_report_success(self):
        with tempfile.TemporaryDirectory() as td:
            s=SourceSearchScraper(output_dir=Path(td)/'o',cache_dir=Path(td)/'c',cookie='x=y',env_path=Path(td)/'missing')
            a=self._build_article('文章A','A1');a['detail_context']={'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                return [{'file_name':'X1','db_name':'','title':'同名'},{'file_name':'X2','db_name':'','title':'同名'}] if file_type=='CBBDREF' else []
            with patch.object(s,'fetch_citation_metrics',return_value={'references':0,'citing_articles':1}),patch.object(s,'fetch_citation_relation_items',side_effect=fetch):
                result=s.enrich_articles([a])[0]
            self.assertEqual(result['citation_network_status'],'partial')

    def test_citation_pages_distinguish_same_title_distinct_file_names(self):
        with tempfile.TemporaryDirectory() as td:
            s=SourceSearchScraper(output_dir=Path(td)/'o',cache_dir=Path(td)/'c',cookie='x=y',env_path=Path(td)/'missing')
            payloads=[{'code':0,'data':{'total':2,'data':[{'title':'同名成果','fileName':fn}]}} for fn in ('X1','X2')]
            with patch.object(s,'fetch_citation_list',side_effect=payloads):
                rows=s.fetch_citation_relation_items({},relation_type='citations',file_type='CBBDREF',page_size=1,max_pages=0)
            self.assertEqual([x['file_name'] for x in rows],['X1','X2'])

    def test_enrich_articles_fetches_all_citation_types_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            citation_items = {
                "CJFDREF": [{"title": "期刊文献", "file_name": "J1", "db_name": "CJFQ"}],
                "CDFDREF": [{"title": "博士论文", "file_name": "D1", "db_name": "CDFD"}],
                "CMFDREF": [{"title": "重复期刊文献", "file_name": "J1", "db_name": "CJFQ"}],
                "CPFDREF": [{"title": "会议论文", "file_name": "P1", "db_name": "CPFD"}],
                "IPFDREF": [{"title": "国际会议论文", "file_name": "I1", "db_name": "IPFD"}],
                "CCNDREF": [],
                "CYFDREF": [],
                "CBBDREF": [{"title": "图书", "file_name": "B1", "db_name": "WBFD"}],
                "WWJDREF": [],
                "SCSDREF": [],
                "SCPDREF": [],
                "NLNKREF": [],
            }
            metrics = {
                "references": 0,
                "citing_articles": 5,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            def fetch_relation_items(article_arg, relation_type, file_type, page_size=10, max_pages=0):
                if relation_type != "citations":
                    raise AssertionError(f"unexpected relation type: {relation_type}")
                self.assertEqual(max_pages, 0)
                return citation_items[file_type]

            with patch.object(scraper, "fetch_citation_metrics", return_value=metrics), patch.object(
                scraper, "fetch_citation_relation_items", side_effect=fetch_relation_items
            ) as fetch_citation_relation_items:
                enriched = scraper.enrich_articles([article], max_relation_pages=0)

        citation_calls = [
            call
            for call in fetch_citation_relation_items.call_args_list
            if call.kwargs["relation_type"] == "citations"
        ]
        self.assertEqual(
            [call.kwargs["file_type"] for call in citation_calls],
            ["CJFDREF", "CDFDREF", "CMFDREF", "CPFDREF", "IPFDREF", "CCNDREF", "CYFDREF", "CBBDREF", "WWJDREF", "SCSDREF", "SCPDREF", "NLNKREF"],
        )
        self.assertEqual([item["file_name"] for item in enriched[0]["citing_articles"]], ["J1", "D1", "P1", "I1", "B1"])
        self.assertEqual(enriched[0]["citation_network_status"], "success")

    def test_enrich_articles_marks_incomplete_first_page_citations_as_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            metrics = {
                "references": 0,
                "citing_articles": 3,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=metrics), patch.object(
                scraper,
                "fetch_citation_relation_items",
                return_value=[{"title": "唯一可见引证", "file_name": "C1", "db_name": "CJFQ"}],
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["citation_network_status"], "partial")
        self.assertIn("expected=3 fetched=1", enriched[0]["citation_network_error"])

    def test_enrich_articles_marks_empty_first_page_citations_as_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            metrics = {
                "references": 0,
                "citing_articles": 3,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=metrics), patch.object(
                scraper, "fetch_citation_relation_items", return_value=[]
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["citation_network_status"], "partial")
        self.assertIn("expected=3 fetched=0", enriched[0]["citation_network_error"])

    def test_enrich_articles_redacts_session_token_from_persisted_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=session-secret&uniplatform=NZKPT",
            }
            metrics = {
                "references": 1,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }
            error = ValueError(
                "request failed: https://kns.cnki.net/restapi/references?"
                "v=session-secret&token=token-secret&sid=sid-secret"
            )

            with patch.object(scraper, "fetch_citation_metrics", return_value=metrics), patch.object(
                scraper, "fetch_citation_relation_items", side_effect=error
            ):
                enriched = scraper.enrich_articles([article])

        persisted_error = enriched[0]["citation_network_error"]
        self.assertNotIn("session-secret", persisted_error)
        self.assertNotIn("token-secret", persisted_error)
        self.assertNotIn("sid-secret", persisted_error)
        self.assertIn("[REDACTED]", persisted_error)

    def test_incomplete_reader_references_recovered_from_all_database_types(self):
        with tempfile.TemporaryDirectory() as td:
            scraper = SourceSearchScraper(output_dir=Path(td)/'out', cache_dir=Path(td)/'cache', cookie='x=y', env_path=Path(td)/'missing')
            article = self._build_article('文章A', 'A1')
            article['detail_context'] = {'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            one = {'file_name':'J1','db_name':'CJFQ','title':'期刊'}
            two = {'file_name':'B1','db_name':'WBFD','title':'图书'}
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                if scraper.reader_references_available: return [one]
                return {'CJFDREF':[one], 'CBBDREF':[two]}.get(file_type,[])
            with patch.object(scraper,'fetch_citation_metrics',return_value={'references':2,'citing_articles':0}), patch.object(scraper,'fetch_citation_relation_items',side_effect=fetch):
                result=scraper.enrich_articles([article])[0]
            self.assertEqual(result['citation_network_status'],'success')
            self.assertEqual({x['file_name'] for x in result['references']},{'J1','B1'})

    def test_reference_recovery_preserves_distinct_books_with_same_title(self):
        with tempfile.TemporaryDirectory() as td:
            scraper=SourceSearchScraper(output_dir=Path(td)/'out',cache_dir=Path(td)/'cache',cookie='x=y',env_path=Path(td)/'missing')
            article=self._build_article('文章A','A1')
            article['detail_context']={'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            books=[{'file_name':'BOOK1','db_name':'','title':'选集'},{'file_name':'BOOK2','db_name':'','title':'选集'}]
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                if scraper.reader_references_available:return books[:1]
                return books if file_type=='CBBDREF' else []
            with patch.object(scraper,'fetch_citation_metrics',return_value={'references':2,'citing_articles':0}), patch.object(scraper,'fetch_citation_relation_items',side_effect=fetch):
                result=scraper.enrich_articles([article])[0]
            self.assertEqual(result['citation_network_status'],'success')
            self.assertEqual([x['file_name'] for x in result['references']],['BOOK1','BOOK2'])

    def test_reader_duplicate_page_can_recover_only_from_complete_independent_api(self):
        with tempfile.TemporaryDirectory() as td:
            s=SourceSearchScraper(output_dir=Path(td)/'o',cache_dir=Path(td)/'c',cookie='x=y',env_path=Path(td)/'missing')
            article=self._build_article('文章A','A1');article['detail_context']={'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            one={'file_name':'YEAR1','db_name':'','title':'年鉴'};two={'file_name':'YEAR2','db_name':'','title':'年鉴'}
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                if s.reader_references_available:raise PartialCitationRelationError('reader repeated',[one])
                return [one,two] if file_type=='CYFDREF' else []
            with patch.object(s,'fetch_citation_metrics',return_value={'references':2,'citing_articles':0}),patch.object(s,'fetch_citation_relation_items',side_effect=fetch):
                result=s.enrich_articles([article])[0]
            self.assertEqual(result['citation_network_status'],'success')
            self.assertEqual([x['file_name'] for x in result['references']],['YEAR1','YEAR2'])

    def test_failed_independent_recovery_preserves_original_reader_partial(self):
        with tempfile.TemporaryDirectory() as td:
            s=SourceSearchScraper(output_dir=Path(td)/'o',cache_dir=Path(td)/'c',cookie='x=y',env_path=Path(td)/'missing')
            a=self._build_article('文章A','A1');a['detail_context']={'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            item={'file_name':'YEAR1','db_name':'','title':'年鉴'}
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                if s.reader_references_available:raise PartialCitationRelationError('reader repeated',[item])
                if file_type=='CYFDREF':raise ValueError('database unavailable')
                return [item] if file_type=='CJFDREF' else []
            with patch.object(s,'fetch_citation_metrics',return_value={'references':2,'citing_articles':0}),patch.object(s,'fetch_citation_relation_items',side_effect=fetch):
                result=s.enrich_articles([a])[0]
            self.assertEqual(result['citation_network_status'],'partial')
            self.assertIn('reader repeated',result['citation_network_error'])
            self.assertEqual(result['references'],[item])
            self.assertTrue(s.reader_references_available)

    def test_reference_database_failure_cannot_become_success(self):
        with tempfile.TemporaryDirectory() as td:
            scraper=SourceSearchScraper(output_dir=Path(td)/'out',cache_dir=Path(td)/'cache',cookie='x=y',env_path=Path(td)/'missing')
            article=self._build_article('文章A','A1')
            article['detail_context']={'api_host':'https://kns.cnki.net/restapi','kcms_link':'v=test'}
            one={'file_name':'J1','db_name':'CJFQ','title':'期刊'}
            def fetch(a,relation_type,file_type,page_size=10,max_pages=0):
                if scraper.reader_references_available:return [one]
                if file_type=='CDFDREF':raise ValueError('source failed')
                return [one] if file_type=='CJFDREF' else []
            with patch.object(scraper,'fetch_citation_metrics',return_value={'references':2,'citing_articles':0}), patch.object(scraper,'fetch_citation_relation_items',side_effect=fetch):
                result=scraper.enrich_articles([article])[0]
            self.assertEqual(result['citation_network_status'],'partial')
            self.assertEqual(len(result['references']),1)
            self.assertTrue(scraper.reader_references_available)

    def test_enrich_articles_marks_incomplete_references_as_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            metrics = {
                "references": 52,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }
            partial_references = [
                {"title": f"参考文献{index}", "file_name": f"R{index}", "db_name": "CJFQ"}
                for index in range(50)
            ]

            with patch.object(scraper, "fetch_citation_metrics", return_value=metrics), patch.object(
                scraper, "fetch_citation_relation_items", return_value=partial_references
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["citation_network_status"], "partial")
        self.assertIn("expected=52 fetched=50", enriched[0]["citation_network_error"])

    def test_enrich_articles_fetches_details_only_for_references_and_citations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            payload_by_relation = {
                "references": self._build_relation_payload("参考文献A", "JYYJ202001001"),
                "citations": self._build_relation_payload("引证文献A", "YZWX202001001"),
            }

            def fake_fetch(article_arg, relation_type="references", file_type="CJFDREF", page=1, page_size=10):
                if relation_type not in payload_by_relation:
                    raise AssertionError(f"unexpected fetch for {relation_type}")
                return payload_by_relation[relation_type]

            fake_metrics = {
                "references": 1,
                "citing_articles": 1,
                "co_cited_articles": 1,
                "co_citing_articles": 1,
                "secondary_references": 1,
                "secondary_citing_articles": 1,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_citation_list", side_effect=fake_fetch
            ) as fetch_citation_list:
                enriched = scraper.enrich_articles([article])

        called_relation_types = {call.kwargs["relation_type"] for call in fetch_citation_list.call_args_list}
        self.assertEqual(called_relation_types, {"references", "citations"})
        self.assertEqual(enriched[0]["citation_network_status"], "success")
        self.assertEqual(enriched[0]["references"][0]["title"], "参考文献A")
        self.assertEqual(enriched[0]["citing_articles"][0]["title"], "引证文献A")
        self.assertEqual(enriched[0]["co_cited_articles"], [])
        self.assertEqual(enriched[0]["co_citing_articles"], [])
        self.assertEqual(enriched[0]["secondary_references"], [])
        self.assertEqual(enriched[0]["secondary_citing_articles"], [])

    def test_enrich_articles_skips_zero_count_relations_reported_by_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }

            def fake_fetch(article_arg, relation_type="references", file_type="CJFDREF", page=1, page_size=10):
                if relation_type != "references":
                    raise AssertionError(f"unexpected fetch for {relation_type}")
                return self._build_relation_payload("参考文献A", "JYYJ202001001")

            fake_metrics = {
                "references": 1,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_citation_list", side_effect=fake_fetch
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["citation_network_status"], "success")
        self.assertEqual(enriched[0]["references"][0]["title"], "参考文献A")
        self.assertEqual(enriched[0]["citing_articles"], [])
        self.assertEqual(enriched[0]["co_cited_articles"], [])
        self.assertEqual(enriched[0]["co_citing_articles"], [])
        self.assertEqual(enriched[0]["secondary_references"], [])
        self.assertEqual(enriched[0]["secondary_citing_articles"], [])

    def test_enrich_articles_builds_detail_context_from_detail_url_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_url"] = "https://kns.cnki.net/detail"
            article.pop("detail_context", None)
            detail_html = """
            <input id="apiHost" type="hidden" value="https://kns.cnki.net/restapi"/>
            <input id="paramkcmslink" type="hidden" value="v=test&amp;uniplatform=NZKPT"/>
            <input id="paramfilename" type="hidden" value="A1"/>
            <input id="paramdbcode" type="hidden" value="DB1"/>
            <input id="paramdbname" type="hidden" value="DBNAME1"/>
            <input id="paramresource" type="hidden" value="JOURNAL"/>
            """
            fake_metrics = {
                "references": 0,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_detail_page", return_value=detail_html), patch.object(
                scraper, "fetch_citation_metrics", return_value=fake_metrics
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["detail_context"]["kcms_link"], "v=test&uniplatform=NZKPT")
        self.assertEqual(enriched[0]["citation_network_status"], "success")
        self.assertEqual(enriched[0]["citation_metrics"], fake_metrics)

    def test_ensure_detail_context_refreshes_session_after_first_forbidden_detail_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_url"] = "https://kns.cnki.net/detail"
            article["detail_context"] = {}
            detail_html = """
            <input id="paramkcmslink" type="hidden" value="v=refreshed&amp;uniplatform=NZKPT"/>
            """

            with patch.object(
                scraper,
                "fetch_detail_page",
                side_effect=[ValueError("Unexpected status code: 403"), detail_html],
            ) as fetch_detail_page, patch.object(scraper, "ensure_session") as ensure_session:
                context = scraper.ensure_detail_context(article)

        self.assertEqual(context["kcms_link"], "v=refreshed&uniplatform=NZKPT")
        self.assertEqual(fetch_detail_page.call_count, 2)
        self.assertEqual(ensure_session.call_count, 1)

    def test_enrich_articles_propagates_cookie_replacement_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")

            with patch.object(
                scraper,
                "ensure_detail_context",
                side_effect=CnkiCookieReplacementRequired("需要人工更新 Cookie"),
            ):
                with self.assertRaises(CnkiCookieReplacementRequired):
                    scraper.enrich_articles([article])

    def test_enrich_articles_backfills_empty_abstract_from_detail_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["abstract"] = ""
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
                "abstract": "详情页摘要",
            }
            fake_metrics = {
                "references": 0,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["abstract"], "详情页摘要")

    def test_enrich_articles_backfills_empty_detail_metadata_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["institution"] = ""
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
                "institution": "北京大学教育学院",
                "keywords": "教育治理；数字化转型",
                "funding": "国家社会科学基金项目",
                "album": "社会科学Ⅱ辑",
                "special_topic": "教育理论与教育管理",
                "classification_number": "G40",
            }
            fake_metrics = {
                "references": 0,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(enriched[0]["institution"], "北京大学教育学院")
        self.assertEqual(enriched[0]["keywords"], "教育治理；数字化转型")
        self.assertEqual(enriched[0]["funding"], "国家社会科学基金项目")
        self.assertEqual(enriched[0]["album"], "社会科学Ⅱ辑")
        self.assertEqual(enriched[0]["special_topic"], "教育理论与教育管理")
        self.assertEqual(enriched[0]["classification_number"], "G40")

    def test_enrich_articles_uses_reader_detail_vid_for_full_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
                "dbname": "cjfdlast2024",
            }
            fake_metrics = {
                "references": 32,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }
            reader_detail = {"metadata": [{"name": "vid", "value": "reader-token"}]}
            reader_payload = self._build_relation_payloads(
                [(f"参考文献{idx}", f"A{idx}") for idx in range(1, 33)],
                total=32,
            )

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_reader_detail", return_value=reader_detail
            ) as fetch_reader_detail, patch.object(scraper, "fetch_reader_references", return_value=reader_payload), patch.object(
                scraper, "fetch_citation_list", return_value=self._build_relation_payloads([], total=0)
            ):
                enriched = scraper.enrich_articles([article], relation_page_size=10, max_relation_pages=0)

        self.assertEqual(enriched[0]["citation_network_status"], "success")
        self.assertEqual(len(enriched[0]["references"]), 32)
        self.assertEqual(enriched[0]["reader_detail"], reader_detail)
        fetch_reader_detail.assert_called_once()

    def test_enrich_articles_uses_primary_type_for_references_and_all_types_for_citations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "uniplatform=NZKPT",
                "dbname": "cjfdlast2024",
            }
            fake_metrics = {
                "references": 1,
                "citing_articles": 1,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_reader_detail", side_effect=ValueError("登录已过期")
            ), patch.object(scraper, "refresh_session"), patch.object(
                scraper, "fetch_citation_list", return_value=self._build_relation_payload("文献A", "R1")
            ) as fetch_citation_list:
                enriched = scraper.enrich_articles([article], relation_page_size=10, max_relation_pages=1)

        self.assertEqual(enriched[0]["citation_network_status"], "success")
        self.assertEqual(
            [call.kwargs["file_type"] for call in fetch_citation_list.call_args_list],
            ["CJFDREF", "CJFDREF", "CDFDREF", "CMFDREF", "CPFDREF", "IPFDREF", "CCNDREF", "CYFDREF", "CBBDREF", "WWJDREF", "SCSDREF", "SCPDREF", "NLNKREF"],
        )
        self.assertEqual(
            [call.kwargs["relation_type"] for call in fetch_citation_list.call_args_list],
            ["references", "citations", "citations", "citations", "citations", "citations", "citations", "citations", "citations", "citations", "citations", "citations", "citations"],
        )

    def test_reference_pages_allow_same_title_with_distinct_filenames_without_database(self):
        with tempfile.TemporaryDirectory() as td:
            s=SourceSearchScraper(output_dir=Path(td)/'o',cache_dir=Path(td)/'c',cookie='x=y',env_path=Path(td)/'missing')
            payloads=[]
            for name in ('YEAR1','YEAR2'):
                payloads.append({'code':0,'data':{'total':2,'data':[{'title':'中国城市统计年鉴','fileName':name}]}})
            with patch.object(s,'fetch_citation_list',side_effect=payloads):
                rows=s.fetch_citation_relation_items({},relation_type='references',file_type='CYFDREF',page_size=1,max_pages=0)
            self.assertEqual([x['file_name'] for x in rows],['YEAR1','YEAR2'])

    def test_fetch_citation_relation_items_stops_on_repeated_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {"detail_context": {"api_host": "https://kns.cnki.net/restapi", "kcms_link": "v=test"}}
            payload = self._build_relation_payloads(
                [(f"重复页文献{index}", f"R{index}") for index in range(1, 11)],
                total=20,
            )

            with patch.object(scraper, "fetch_citation_list", side_effect=[payload, payload]) as fetch:
                with self.assertRaisesRegex(ValueError, "重复"):
                    scraper.fetch_citation_relation_items(
                        article,
                        relation_type="citations",
                        file_type="CJFDREF",
                        max_pages=0,
                    )

        self.assertEqual(fetch.call_count, 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=test&uniplatform=NZKPT",
                }
            }
            payloads = [
                self._build_relation_payloads([("参考文献A", "A1"), ("参考文献B", "B1")], total=3),
                self._build_relation_payloads([("参考文献C", "C1")], total=3),
            ]

            with patch.object(scraper, "fetch_citation_list", side_effect=payloads) as fetch_citation_list:
                items = scraper.fetch_citation_relation_items(
                    article,
                    relation_type="references",
                    file_type="CJFDREF",
                    page_size=2,
                    max_pages=0,
                )

        self.assertEqual([item["title"] for item in items], ["参考文献A", "参考文献B", "参考文献C"])
        self.assertEqual([call.kwargs["page"] for call in fetch_citation_list.call_args_list], [1, 2])
        self.assertEqual([call.kwargs["page_size"] for call in fetch_citation_list.call_args_list], [2, 2])

    def test_enrich_articles_keeps_partial_relation_items_when_later_page_is_limited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            first_page = self._build_relation_payloads(
                [(f"参考文献{idx}", f"A{idx}") for idx in range(1, 11)],
                total=11,
            )
            empty_page = self._build_relation_payloads([], total=0)
            fake_metrics = {
                "references": 11,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            def fake_fetch(article_arg, relation_type="references", file_type="CJFDREF", page=1, page_size=10):
                if file_type != "CJFDREF":
                    return empty_page
                if page == 1:
                    return first_page
                return {"code": 403, "message": "登录失效", "data": None}

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_reader_detail", return_value={}
            ), patch.object(scraper, "fetch_citation_list", side_effect=fake_fetch):
                enriched = scraper.enrich_articles([article], relation_page_size=10, max_relation_pages=0)

        self.assertEqual(enriched[0]["citation_network_status"], "partial")
        self.assertIn("登录失效", enriched[0]["citation_network_error"])
        self.assertEqual(len(enriched[0]["references"]), 10)
        self.assertEqual(enriched[0]["references"][0]["title"], "参考文献1")
        self.assertEqual(enriched[0]["references"][-1]["title"], "参考文献10")

    def test_enrich_articles_retries_detail_context_after_401(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_url"] = "https://kns.cnki.net/detail"
            article.pop("detail_context", None)
            detail_html = """
            <input id="apiHost" type="hidden" value="https://kns.cnki.net/restapi"/>
            <input id="paramkcmslink" type="hidden" value="v=test&amp;uniplatform=NZKPT"/>
            <input id="paramfilename" type="hidden" value="A1"/>
            <input id="paramdbcode" type="hidden" value="DB1"/>
            <input id="paramdbname" type="hidden" value="DBNAME1"/>
            <input id="paramresource" type="hidden" value="JOURNAL"/>
            """
            fake_metrics = {
                "references": 0,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_detail_page", side_effect=[ValueError("Unexpected status code: 401"), detail_html]) as fetch_detail_page, patch.object(
                scraper, "fetch_citation_metrics", return_value=fake_metrics
            ), patch.object(scraper, "refresh_session"):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(fetch_detail_page.call_count, 2)
        self.assertEqual(enriched[0]["detail_context"]["kcms_link"], "v=test&uniplatform=NZKPT")
        self.assertEqual(enriched[0]["citation_network_status"], "success")

    def test_enrich_articles_retries_citation_metrics_after_401(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
                "dbname": "CJFDLAST2024",
            }
            request_log = {"metrics": 0, "index": 0}
            metrics_401 = self._build_response({"code": 401, "message": "登录失效", "data": None}, 401)
            metrics_ok = self._build_response({"code": 0, "data": {"references": 0, "citations": 0, "co-references": 0, "co-citations": 0, "second-references": 0, "second-citations": 0}}, 200)
            index_ok = self._build_response("<html></html>", 200)

            def fake_request_with_retry(method, url, **kwargs):
                if "quotations/metrics" in url:
                    request_log["metrics"] += 1
                    return metrics_401 if request_log["metrics"] == 1 else metrics_ok
                if "defaultresult/index" in url:
                    request_log["index"] += 1
                    return index_ok
                raise AssertionError(f"unexpected request: {url}")

            with patch.object(scraper, "ensure_detail_context", return_value=article["detail_context"]) as ensure_detail_context, patch.object(
                scraper, "request_with_retry", side_effect=fake_request_with_retry
            ):
                enriched = scraper.enrich_articles([article])

        self.assertEqual(ensure_detail_context.call_count, 2)
        self.assertEqual(request_log["metrics"], 2)
        self.assertEqual(request_log["index"], 1)
        self.assertEqual(enriched[0]["citation_network_status"], "success")

    def test_fetch_citation_list_refreshes_detail_context_and_rebuilds_request_after_403(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_url": "https://kns.cnki.net/detail",
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=old-token&uniplatform=NZKPT",
                },
            }
            refreshed_detail_context = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=new-token&uniplatform=NZKPT",
            }
            payload = {"code": 0, "data": {"total": 0, "data": []}}
            requested_urls: list[str] = []

            def fake_request_with_retry(method, url, **kwargs):
                requested_urls.append(url)
                if len(requested_urls) == 1:
                    raise ValueError("Request failed after retries: 403 https://example.com")
                return self._build_response(payload, 200)

            with patch.object(scraper, "request_with_retry", side_effect=fake_request_with_retry), patch.object(
                scraper, "ensure_session"
            ) as ensure_session, patch.object(scraper, "ensure_detail_context", return_value=refreshed_detail_context) as ensure_detail_context:
                result = scraper.fetch_citation_list(article, relation_type="references", file_type="CJFDREF")

        self.assertEqual(result, payload)
        self.assertIn("v=old-token", requested_urls[0])
        self.assertIn("v=new-token", requested_urls[1])
        self.assertEqual(ensure_session.call_count, 1)
        ensure_detail_context.assert_called_once_with(article)

    def test_fetch_citation_relation_items_falls_back_when_reader_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=reader-token&uniplatform=NZKPT",
                    "dbname": "CJFDLAST2024",
                },
                "reader_detail": {"metadata": [{"name": "vid", "value": "reader-token"}]},
            }
            empty_reader_payload = self._build_relation_payloads([], total=0)
            fallback_payload = self._build_relation_payloads([("参考文献A", "A1")], total=1)

            with patch.object(scraper, "fetch_reader_references", return_value=empty_reader_payload) as fetch_reader_references, patch.object(
                scraper, "fetch_citation_list", return_value=fallback_payload
            ) as fetch_citation_list:
                items = scraper.fetch_citation_relation_items(
                    article,
                    relation_type="references",
                    file_type="CJFDREF",
                    page_size=10,
                    max_pages=0,
                )

        self.assertEqual([item["title"] for item in items], ["参考文献A"])
        fetch_reader_references.assert_called_once_with(article, page_size=50, page=1)
        fetch_citation_list.assert_called_once_with(
            article, relation_type="references", file_type="CJFDREF", page=1, page_size=10
        )

    def test_fetch_citation_relation_items_retries_reader_path_after_401(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=old-token&uniplatform=NZKPT",
                    "dbname": "CJFDLAST2024",
                },
            }
            payload = self._build_relation_payloads([("参考文献A", "A1")], total=1)
            refreshed_detail_context = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=new-token&uniplatform=NZKPT",
                "dbname": "CJFDLAST2024",
            }

            with patch.object(scraper, "fetch_reader_detail", side_effect=[ValueError("登录已过期"), refreshed_detail_context]) as fetch_reader_detail, patch.object(
                scraper, "fetch_reader_references", side_effect=[ValueError("Unexpected status code: 401"), payload]
            ) as fetch_reader_references, patch.object(scraper, "_refresh_reader_context", return_value=refreshed_detail_context) as refresh_reader_context, patch.object(
                scraper, "fetch_citation_list", side_effect=AssertionError("should not call paginated citation API")
            ):
                items = scraper.fetch_citation_relation_items(
                    article,
                    relation_type="references",
                    file_type="CJFDREF",
                    page_size=10,
                    max_pages=0,
                )

        self.assertEqual([item["title"] for item in items], ["参考文献A"])
        self.assertEqual(fetch_reader_detail.call_count, 1)
        self.assertEqual(fetch_reader_references.call_count, 2)
        refresh_reader_context.assert_called_once_with(article)

    def test_fetch_citation_relation_items_uses_detail_context_v_when_reader_detail_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=detail-reader-token&uniplatform=NZKPT",
                    "dbname": "CJFDLAST2024",
                },
            }
            payload = self._build_relation_payloads(
                [(f"参考文献{idx}", f"A{idx}") for idx in range(1, 33)],
                total=32,
            )

            with patch.object(scraper, "fetch_reader_detail", side_effect=ValueError("登录已过期")) as fetch_reader_detail, patch.object(
                scraper, "fetch_reader_references", return_value=payload
            ) as fetch_reader_references, patch.object(
                scraper, "fetch_citation_list", side_effect=AssertionError("should not call paginated citation API")
            ):
                items = scraper.fetch_citation_relation_items(
                    article,
                    relation_type="references",
                    file_type="CJFDREF",
                    page_size=10,
                    max_pages=0,
                )

        self.assertEqual(len(items), 32)
        fetch_reader_detail.assert_called_once()
        fetch_reader_references.assert_called_once_with(article, page_size=50, page=1)

    def test_fetch_citation_relation_items_reads_reader_reference_pages_until_total(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "reader_detail": {
                    "metadata": [{"name": "vid", "value": "reader-token"}],
                },
            }
            payloads = [
                self._build_relation_payloads([(f"参考文献{idx}", f"A{idx}") for idx in range(1, 51)], total=52),
                self._build_relation_payloads([("参考文献51", "A51"), ("参考文献52", "A52")], total=52),
            ]

            with patch.object(scraper, "fetch_reader_references", side_effect=payloads) as fetch_reader_references, patch.object(
                scraper, "fetch_citation_list", side_effect=AssertionError("should not call paginated citation API")
            ):
                items = scraper.fetch_citation_relation_items(
                    article,
                    relation_type="references",
                    file_type="CJFDREF",
                    page_size=10,
                    max_pages=0,
                )

        self.assertEqual(len(items), 52)
        self.assertEqual([call.kwargs["page"] for call in fetch_reader_references.call_args_list], [1, 2])

    def test_fetch_citation_relation_items_raises_partial_when_reader_page_repeats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "reader_detail": {
                    "metadata": [{"name": "vid", "value": "reader-token"}],
                },
            }
            first_page = self._build_relation_payloads(
                [(f"参考文献{idx}", f"A{idx}") for idx in range(1, 51)],
                total=52,
            )

            with patch.object(
                scraper,
                "fetch_reader_references",
                side_effect=[first_page, first_page],
            ), patch.object(
                scraper,
                "fetch_citation_list",
                side_effect=AssertionError("should not fall back after partial reader data"),
            ):
                with self.assertRaises(PartialCitationRelationError) as raised:
                    scraper.fetch_citation_relation_items(
                        article,
                        relation_type="references",
                        file_type="CJFDREF",
                        page_size=10,
                        max_pages=0,
                    )

        self.assertEqual(len(raised.exception.items), 50)
        self.assertIn("第2页重复", str(raised.exception))

    def test_fetch_citation_relation_items_raises_partial_when_reader_page_overlaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "reader_detail": {
                    "metadata": [{"name": "vid", "value": "reader-token"}],
                },
            }
            first_page = self._build_relation_payloads(
                [(f"参考文献{idx}", f"A{idx}") for idx in range(1, 51)],
                total=52,
            )
            overlapping_page = self._build_relation_payloads(
                [("参考文献50", "A50"), ("参考文献51", "A51")],
                total=52,
            )

            with patch.object(
                scraper,
                "fetch_reader_references",
                side_effect=[first_page, overlapping_page],
            ), patch.object(
                scraper,
                "fetch_citation_list",
                side_effect=AssertionError("should not fall back after partial reader data"),
            ):
                with self.assertRaises(PartialCitationRelationError) as raised:
                    scraper.fetch_citation_relation_items(
                        article,
                        relation_type="references",
                        file_type="CJFDREF",
                        page_size=10,
                        max_pages=0,
                    )

        self.assertEqual(len(raised.exception.items), 50)
        self.assertIn("第2页重复", str(raised.exception))

    def test_fetch_citation_relation_items_raises_api_message_when_payload_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=test&uniplatform=NZKPT",
                }
            }

            with patch.object(scraper, "fetch_citation_list", return_value={"code": 401, "message": "登录失效", "data": None}):
                with self.assertRaisesRegex(ValueError, "登录失效"):
                    scraper.fetch_citation_relation_items(article, relation_type="references", file_type="CJFDREF")

    def test_fetch_citation_relation_items_raises_for_cnki_business_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = {
                "detail_context": {
                    "api_host": "https://kns.cnki.net/restapi",
                    "kcms_link": "v=test&uniplatform=NZKPT",
                }
            }

            with patch.object(scraper, "fetch_citation_list", return_value={"code": 403, "message": "登录失效", "data": None}):
                with self.assertRaisesRegex(ValueError, "登录失效"):
                    scraper.fetch_citation_relation_items(article, relation_type="references", file_type="CJFDREF")

    def test_run_writes_enriched_jsonl_and_exports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            fake_articles = [self._build_article("文章A", "A1")]
            with patch.object(scraper, "search_one_journal", return_value=fake_articles):
                enriched = scraper.run(["北京大学教育评论"], max_pages=1)

            enriched_jsonl = base_dir / "output" / "enriched.jsonl"
            articles_json = base_dir / "output" / "articles.json"
            articles_csv = base_dir / "output" / "articles.csv"
            citation_relations_csv = base_dir / "output" / "citation_relations.csv"

            self.assertEqual(len(enriched), 1)
            self.assertTrue(enriched_jsonl.exists())
            self.assertTrue(articles_json.exists())
            self.assertTrue(articles_csv.exists())
            self.assertTrue(citation_relations_csv.exists())

    def test_run_defaults_to_full_relation_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            fake_articles = [self._build_article("文章A", "A1")]

            with patch.object(scraper, "search_one_journal", return_value=fake_articles), patch.object(
                scraper, "enrich_articles", return_value=fake_articles
            ) as enrich_articles:
                scraper.run(["北京大学教育评论"], max_pages=1)

        self.assertEqual(enrich_articles.call_args.kwargs["max_relation_pages"], 0)

    def test_run_skips_existing_success_then_retries_failed_and_partial_articles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            existing_success = {**self._build_article("文章A", "A1"), "article_id": "A1:DB1", "citation_network_status": "success"}
            existing_failed = {**self._build_article("文章B", "B1"), "article_id": "B1:DB1", "citation_network_status": "failed"}
            existing_partial = {**self._build_article("文章P", "P1"), "article_id": "P1:DB1", "citation_network_status": "partial"}
            scraper.append_jsonl(output_dir / "enriched.jsonl", existing_success)
            scraper.append_jsonl(output_dir / "enriched.jsonl", existing_failed)
            scraper.append_jsonl(output_dir / "enriched.jsonl", existing_partial)
            search_results = [
                self._build_article("文章A", "A1"),
                self._build_article("文章B", "B1"),
                self._build_article("文章P", "P1"),
                self._build_article("文章C", "C1"),
            ]
            enriched_retry = {**self._build_article("文章B", "B1"), "article_id": "B1:DB1", "citation_network_status": "success"}
            enriched_partial_retry = {**self._build_article("文章P", "P1"), "article_id": "P1:DB1", "citation_network_status": "success"}
            enriched_new = {**self._build_article("文章C", "C1"), "article_id": "C1:DB1", "citation_network_status": "success"}

            with patch.object(scraper, "search_one_journal", return_value=search_results), patch.object(
                scraper, "enrich_articles", side_effect=[[enriched_retry], [enriched_partial_retry], [enriched_new]]
            ) as enrich_articles:
                enriched = scraper.run(["北京大学教育评论"], max_pages=1)

            enriched_lines = (output_dir / "enriched.jsonl").read_text(encoding="utf-8").strip().splitlines()

        self.assertEqual([call.args[0][0]["title"] for call in enrich_articles.call_args_list], ["文章B", "文章P", "文章C"])
        self.assertEqual([article["article_id"] for article in enriched], ["A1:DB1", "B1:DB1", "P1:DB1", "C1:DB1"])
        self.assertEqual(len(enriched_lines), 4)
        self.assertIn("文章C", enriched_lines[-1])

    def test_run_keeps_existing_partial_records_in_aggregate_when_not_found_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            existing_partial = {**self._build_article("文章P", "P1"), "article_id": "P1:DB1", "citation_network_status": "partial"}
            scraper.append_jsonl(output_dir / "enriched.jsonl", existing_partial)

            with patch.object(scraper, "search_one_journal", return_value=[]):
                enriched = scraper.run(["北京大学教育评论"], max_pages=1, resume=True)

        self.assertEqual([article["article_id"] for article in enriched], ["P1:DB1"])
        self.assertEqual(enriched[0]["citation_network_status"], "partial")

    def test_run_can_disable_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            fake_articles = [self._build_article("文章A", "A1")]

            with patch.object(scraper, "search_one_journal", return_value=fake_articles), patch.object(
                scraper, "enrich_articles", return_value=fake_articles
            ) as enrich_articles:
                scraper.run(["北京大学教育评论"], max_pages=1, resume=False)

        self.assertEqual(enrich_articles.call_count, 1)
        self.assertEqual(enrich_articles.call_args.args[0], fake_articles)

    def test_enrich_articles_defaults_to_full_relation_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            article = self._build_article("文章A", "A1")
            article["detail_context"] = {
                "api_host": "https://kns.cnki.net/restapi",
                "kcms_link": "v=test&uniplatform=NZKPT",
            }
            fake_metrics = {
                "references": 1,
                "citing_articles": 0,
                "co_cited_articles": 0,
                "co_citing_articles": 0,
                "secondary_references": 0,
                "secondary_citing_articles": 0,
            }

            with patch.object(scraper, "fetch_citation_metrics", return_value=fake_metrics), patch.object(
                scraper, "fetch_citation_relation_items", return_value=[{"title": "参考文献A"}]
            ) as fetch_citation_relation_items:
                scraper.enrich_articles([article])

        self.assertEqual(fetch_citation_relation_items.call_args.kwargs["max_pages"], 0)

    def test_run_enriches_and_exports_one_journal_before_searching_next_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            events = []

            def fake_search_page(journal_name, page=1, page_size=20, year=""):
                events.append(f"search:{journal_name}")
                return ([self._build_article(f"{journal_name}文章", f"{journal_name}A1")], 1)

            def fake_enrich(articles, relation_page_size=10, max_relation_pages=0):
                events.append(f"enrich:{articles[0]['source_journal_input']}")
                return [{**articles[0], "article_id": f"{articles[0]['file_name']}:DB1", "citation_network_status": "success"}]

            def fake_export(articles, output_dir):
                events.append(f"export:{articles[-1]['source_journal_input']}")

            with patch.object(scraper, "search_page", side_effect=fake_search_page), patch.object(
                scraper, "enrich_articles", side_effect=fake_enrich
            ), patch("scrapers.source_search_runner.export_all_formats", side_effect=fake_export):
                scraper.run(["期刊A", "期刊B"], max_pages=1)

        self.assertEqual(events[:4], ["search:期刊A", "enrich:期刊A", "export:期刊A", "search:期刊B"])

    def test_run_creates_incremental_and_aggregate_outputs_per_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            articles = [self._build_article("文章A", "A1"), self._build_article("文章B", "B1")]

            with patch.object(scraper, "search_page", return_value=(articles, 2)), patch.object(
                scraper,
                "enrich_articles",
                side_effect=lambda pending, **kwargs: [
                    {
                        **pending[0],
                        "article_id": f"{pending[0]['file_name']}:DB1",
                        "citation_network_status": "success",
                        "references": [{"title": "参考文献A"}],
                    }
                ],
            ):
                scraper.run(["北京大学教育评论"], max_pages=1)

            expected_files = ["raw.jsonl", "enriched.jsonl", "articles.jsonl", "articles.json", "articles.csv", "citation_relations.csv"]
            self.assertTrue(all((output_dir / file_name).exists() for file_name in expected_files))

    def test_run_enriches_each_year_slice_before_searching_next_slice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            events = []

            def fake_search_page(journal_name, page=1, page_size=20, year=""):
                events.append(f"search:{year}")
                return ([self._build_article(f"{year}文章", f"F{year}")], 1)

            def fake_enrich(articles, relation_page_size=10, max_relation_pages=0):
                events.append(f"enrich:{articles[0]['title']}")
                return [{**articles[0], "article_id": f"{articles[0]['file_name']}:DB1", "citation_network_status": "success"}]

            with patch.object(scraper, "search_page", side_effect=fake_search_page), patch.object(
                scraper, "enrich_articles", side_effect=fake_enrich
            ):
                scraper.run(["北京大学教育评论"], page_size=100, max_pages=0, year_slices=["2025", "2024"])

        self.assertEqual(events[:4], ["search:2025", "enrich:2025文章", "search:2024", "enrich:2024文章"])

    def test_run_enriches_adaptive_slices_after_search_plan_is_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            scraper = SourceSearchScraper(
                output_dir=base_dir / "output",
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            events = []
            left_articles = [self._build_article(f"上半年文章{index}", f"L{index}") for index in range(40)]
            right_articles = [self._build_article(f"下半年文章{index}", f"R{index}") for index in range(40)]
            root_preview = [self._build_article(f"预览{index}", f"P{index}") for index in range(50)]

            def fake_search_page(journal_name, page=1, page_size=20, year=""):
                events.append(f"search:{year}")
                responses = {
                    "2025": (root_preview, 80),
                    "2025-01-01..2025-07-02": (left_articles, 40),
                    "2025-07-03..2025-12-31": (right_articles, 40),
                }
                return responses[year]

            def fake_enrich(articles, relation_page_size=10, max_relation_pages=0):
                events.append(f"enrich:{articles[0]['title']}")
                return [{**articles[0], "article_id": f"{articles[0]['file_name']}:DB1", "citation_network_status": "success"}]

            with patch.object(scraper, "search_page", side_effect=fake_search_page), patch.object(
                scraper, "enrich_articles", side_effect=fake_enrich
            ):
                scraper.run(["北京大学教育评论"], page_size=50, max_pages=0, year_slices=["2025"])

        self.assertLess(events.index("search:2025-07-03..2025-12-31"), events.index("enrich:上半年文章0"))
        self.assertIn("enrich:下半年文章0", events)

    def test_run_skips_checkpoint_completed_journals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            output_dir.mkdir(parents=True)
            checkpoint_path = output_dir / "checkpoint.json"
            checkpoint_path.write_text(
                '{"shard_id": 1, "shard_count": 1, "current_journal": "", "completed_journals": ["期刊A"], "updated_at": "2026-06-17T00:00:00+08:00"}',
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )

            with patch.object(scraper, "search_page", return_value=([self._build_article("文章B", "B1")], 1)) as search_page, patch.object(
                scraper, "enrich_articles", side_effect=lambda pending, **kwargs: [{**pending[0], "article_id": "B1:DB1", "citation_network_status": "success"}]
            ):
                scraper.run(["期刊A", "期刊B"], max_pages=1, resume=True)

        self.assertEqual([call.args[0] for call in search_page.call_args_list], ["期刊B"])

    def test_run_does_not_complete_truncated_traditional_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=output_dir / "checkpoint.json",
            )
            article = self._build_article("文章A", "A1")
            enriched = {**article, "article_id": "A1:DB1", "citation_network_status": "success"}

            with patch.object(scraper, "search_page", return_value=([article], 2)), patch.object(
                scraper, "enrich_articles", return_value=[enriched]
            ):
                scraper.run(["期刊A"], max_pages=1, resume=True)

            checkpoint = (output_dir / "checkpoint.json").read_text(encoding="utf-8")

        self.assertNotIn("期刊A", checkpoint)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )

            with patch.object(scraper, "search_one_journal", return_value=[]):
                scraper.run(["期刊A"], max_pages=1, resume=True)

            checkpoint_text = checkpoint_path.read_text(encoding="utf-8")

        self.assertNotIn("期刊A", checkpoint_text)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )
            article = self._build_article("文章A", "A1")

            with patch.object(scraper, "search_one_journal", return_value=[article]), patch.object(
                scraper, "enrich_articles", return_value=[{**article, "article_id": "A1:DB1", "citation_network_status": "partial"}]
            ):
                scraper.run(["期刊A"], max_pages=1, resume=True)

            checkpoint_text = checkpoint_path.read_text(encoding="utf-8")

        self.assertNotIn("期刊A", checkpoint_text)

    def test_run_accepts_confirmed_empty_journal_as_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )

            def search_empty(*args, **kwargs):
                scraper.last_search_confirmed_empty = True
                scraper.last_search_complete = True
                return []

            with patch.object(scraper, "search_one_journal", side_effect=search_empty):
                scraper.run(["期刊A"], max_pages=1, resume=True)

            checkpoint_text = checkpoint_path.read_text(encoding="utf-8")

        self.assertIn("期刊A", checkpoint_text)

    def test_run_rejects_incompatible_checkpoint_shard_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            output_dir.mkdir(parents=True)
            checkpoint_path = output_dir / "checkpoint.json"
            checkpoint_path.write_text(
                '{"shard_id": 1, "shard_count": 9, "completed_journals": ["期刊A"]}',
                encoding="utf-8",
            )
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
                shard_id=1,
                shard_count=3,
            )

            with self.assertRaisesRegex(RuntimeError, "shard_count=9"):
                scraper.run(["期刊A"], resume=True)

    def test_run_compacts_and_reexports_resume_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
            )
            first = {**self._build_article("文章A", "A1"), "article_id": "A1:DB1", "citation_network_status": "partial"}
            latest = {**self._build_article("文章A", "A1"), "article_id": "A1:DB1", "citation_network_status": "success"}
            scraper.append_jsonl(output_dir / "enriched.jsonl", first)
            scraper.append_jsonl(output_dir / "enriched.jsonl", latest)

            with patch.object(scraper, "search_one_journal", return_value=[]):
                scraper.run(["期刊A"], max_pages=1, resume=True)

            lines = (output_dir / "enriched.jsonl").read_text(encoding="utf-8").strip().splitlines()
            articles = json.loads((output_dir / "articles.json").read_text(encoding="utf-8"))

        self.assertEqual(len(lines), 1)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["citation_network_status"], "success")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            output_dir = base_dir / "output"
            checkpoint_path = output_dir / "checkpoint.json"
            scraper = SourceSearchScraper(
                output_dir=output_dir,
                cache_dir=base_dir / "cache",
                cookie="cookie=value",
                env_path=base_dir / ".env",
                checkpoint_path=checkpoint_path,
            )
            articles = [self._build_article("文章A", "A1"), self._build_article("文章B", "B1")]

            with patch.object(scraper, "search_page", return_value=(articles, 2)), patch.object(
                scraper,
                "enrich_articles",
                side_effect=lambda pending, **kwargs: [{**pending[0], "article_id": f"{pending[0]['file_name']}:DB1", "citation_network_status": "success"}],
            ):
                scraper.run(["期刊A"], max_pages=1, resume=True, limit_articles=1)

            checkpoint_text = checkpoint_path.read_text(encoding="utf-8")

        self.assertNotIn("期刊A", checkpoint_text)

    def _build_override_scraper(self, base_dir: Path) -> SourceSearchScraper:
        config_path = base_dir / "journal_overrides.json"
        config_path.write_text(
            json.dumps({"系统工程": {"operator": "0", "slices": "monthly"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        return SourceSearchScraper(
            output_dir=base_dir / "output",
            cache_dir=base_dir / "cache",
            cookie="cookie=value",
            env_path=base_dir / ".env",
            journal_overrides_path=config_path,
        )

    def _build_article(self, title: str, file_name: str) -> dict:
        return {
            "title": title,
            "authors": "张三",
            "journal": "北京大学教育评论",
            "publicationTime": "2026-01-01",
            "abstract": "摘要",
            "quote_frequency": "1",
            "download_count": "2",
            "file_name": file_name,
            "db_name": "DB1",
            "db_type": "JOURNAL",
            "source_journal_input": "北京大学教育评论",
            "search_mode": "source",
            "citation_network_status": "pending",
        }

    def _build_response(self, payload, status_code: int):
        class Response:
            def __init__(self, payload, status_code: int) -> None:
                self.payload = payload
                self.status_code = status_code
                self.text = payload if isinstance(payload, str) else ""
                self.url = "https://example.com"

            def json(self):
                return self.payload

        return Response(payload, status_code)

    def _build_relation_payload(self, title: str, file_name: str) -> dict:
        return self._build_relation_payloads([(title, file_name)], total=1)

    def _build_relation_payloads(self, entries: list[tuple[str, str]], total: int) -> dict:
        return {
            "code": 0,
            "data": {
                "total": total,
                "data": [
                    {
                        "metadata": [
                            {"name": "TI", "value": title},
                            {"name": "AU", "value": "李四;"},
                            {"name": "LY", "value": "教育研究"},
                            {"name": "YE", "value": "2020"},
                            {"name": "FN", "value": file_name},
                            {"name": "TN", "value": "CJFDLAST2020"},
                            {"name": "resource", "value": "JOURNAL"},
                        ]
                    }
                    for title, file_name in entries
                ],
            },
        }


if __name__ == "__main__":
    unittest.main()
