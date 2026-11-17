from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from hashlib import sha256
from hmac import new as new_hmac
from http.cookies import SimpleCookie
from pathlib import Path
from secrets import token_bytes
from typing import Any, Callable
from urllib.parse import urlparse
import json
import re
import sqlite3

from config import CNKI_INDEX_URL, CNKI_SEARCH_API_URL, load_cookie_from_env, load_cookie_from_file
from notifications import send_cookie_replacement_email
from exporters import (
    RELATION_FIELDS,
    export_all_formats,
    export_all_formats_from_jsonl,
    export_article_incrementally,
)
from scrapers.checkpoint import empty_checkpoint, load_checkpoint, save_checkpoint, stamp_checkpoint
from scrapers.base_scraper import BaseScraper
from scrapers.cnki_citation_network import (
    REFERENCE_FILE_TYPE,
    RELATION_TYPE_FIELD_MAP,
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
from scrapers.source_search_scraper import (
    build_brief_grid_payload,
    is_explicit_no_content_response,
    parse_brief_grid_response,
)

DETAIL_METADATA_FIELDS = [
    "institution",
    "keywords",
    "funding",
    "album",
    "special_topic",
    "classification_number",
]

DETAIL_RELATION_TYPES = ("references", "citations")
FALLBACK_RELATION_FILE_TYPES = (REFERENCE_FILE_TYPE,)
CITATION_FILE_TYPES = (
    "CJFDREF",
    "CDFDREF",
    "CMFDREF",
    "CPFDREF",
    "IPFDREF",
    "CCNDREF",
    "CYFDREF",
    "CBBDREF",
    "WWJDREF",
    "SCSDREF",
    "SCPDREF",
    "NLNKREF",
)
AUTH_COOLDOWN_SECONDS = 300
MAX_UNBOUNDED_RELATION_PAGES = 10000
RESPONSE_LOG_LIMIT = 500
ALLOWED_CNKI_HOSTS = {"kns.cnki.net", "wap.cnki.net"}
COOKIE_DOMAIN = ".cnki.net"
JOURNAL_OVERRIDES_FILENAME = "journal_overrides.json"
DEFAULT_SOURCE_OPERATOR = "FUZZY"
SUPPORTED_SLICE_STRATEGIES = frozenset({"monthly"})


def _split_cookie_parts(cookie: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_quotes = False
    escaped = False
    for index, character in enumerate(cookie):
        if character == "\\" and in_quotes and not escaped:
            escaped = True
            continue
        if character == '"' and not escaped:
            in_quotes = not in_quotes
        if character == ";" and not in_quotes:
            parts.append(cookie[start:index])
            start = index + 1
        escaped = False
    parts.append(cookie[start:])
    return parts


def parse_cookie_header(cookie: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in _split_cookie_parts(cookie):
        if "=" not in part:
            continue
        name, raw_value = part.split("=", 1)
        name = name.strip()
        raw_value = raw_value.strip()
        if not name or any(ord(character) < 32 for character in name + raw_value):
            continue
        standard_cookie = SimpleCookie()
        standard_cookie.load(f"{name}={raw_value}")
        morsel = standard_cookie.get(name)
        parsed[name] = morsel.value if morsel is not None else raw_value
    return parsed


def relation_item_identity(item: dict[str, Any]) -> tuple[str, ...]:
    filename = item.get('file_name')
    if filename:
        return ('file', filename, item.get('db_name') or '')
    return ('article-id', build_article_id(item))


def deduplicate_relation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated_items: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, ...]] = set()
    for item in items:
        item_id = relation_item_identity(item)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        deduplicated_items.append(item)
    return deduplicated_items


def sanitize_error_message(error: Exception | str) -> str:
    return re.sub(
        r"(?i)([?&](?:v|token|sid|session|cookie|authorization)=)[^&\s'\"]+",
        r"\1[REDACTED]",
        str(error),
    )


class PartialCitationRelationError(ValueError):
    def __init__(self, message: str, items: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.items = items


class CnkiVerificationRequired(RuntimeError):
    """CNKI requires an interactive verification or refreshed authenticated session."""


class CnkiCookieReplacementRequired(CnkiVerificationRequired):
    """CNKI rejected the complete reloaded Cookie on a stable authentication boundary."""



def normalize_journal_name(name: str) -> str:
    """CNKI 在刊名中夹带空格，比较前统一去除。"""
    return re.sub(r"\s+", "", name or "")


def journal_source_matches(actual: str, expected: str) -> bool:
    """CNKI 的文献来源只支持模糊匹配，需在客户端做精确校验。

    返回 True 表示保留该文章。来源缺失时保留，避免误删真实数据。
    """
    if not actual:
        return True
    return normalize_journal_name(actual) == normalize_journal_name(expected)


def load_journal_overrides(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """读取按刊名生效的检索覆盖配置；文件缺失或不合法时返回空配置。"""
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
            continue
        entry: dict[str, Any] = {}
        operator = value.get("operator")
        if isinstance(operator, (str, int)) and str(operator).strip():
            entry["operator"] = str(operator).strip()
        strategy = value.get("slices")
        if isinstance(strategy, str) and strategy in SUPPORTED_SLICE_STRATEGIES:
            entry["slices"] = strategy
        if entry:
            overrides[normalize_journal_name(name)] = entry
    return overrides


def deduplicate_confirmed_source_rows(rows, journal_name):
    """Only collapse the independently verified CNKI duplicate, never conflicts."""
    known_ids = {
        ("对外传播", "DWDC201707027:CJFQ"),
        ("宏观质量研究", "HGLY20261020006:CAPJ"),
    }
    fields = ("file_name", "db_name", "title", "authors", "journal", "publicationTime")
    kept, seen, removed = [], {}, 0
    for article in rows:
        aid = build_article_id(article)
        previous = seen.get(aid)
        if ((journal_name, aid) in known_ids and previous is not None
                and all(article.get(k) == previous.get(k) for k in fields)):
            removed += 1
            continue
        kept.append(article)
        seen[aid] = article
    return kept, removed


class SourceSearchScraper(BaseScraper):
    def __init__(
        self,
        output_dir,
        cache_dir,
        cookie: str = "",
        env_path: str | Path | None = None,
        delay_min: float = 15.0,
        delay_max: float = 20.0,
        max_retries: int = 3,
        backoff_factor: float = 30.0,
        checkpoint_path: str | Path | None = None,
        shard_id: int = 1,
        shard_count: int = 1,
        journal_overrides_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            output_dir=output_dir,
            cache_dir=cache_dir,
            delay_min=delay_min,
            delay_max=delay_max,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )
        self.env_path = Path(env_path or Path(__file__).resolve().parents[1] / ".env")
        self.shard_id = shard_id
        self.shard_count = shard_count
        overrides_path = journal_overrides_path or (
            Path(__file__).resolve().parents[1] / "config" / JOURNAL_OVERRIDES_FILENAME
        )
        self.journal_overrides = load_journal_overrides(overrides_path)
        file_cookie = load_cookie_from_file(self.env_path, shard_id=shard_id)
        self.cookie = cookie or load_cookie_from_env(shard_id=shard_id) or file_cookie
        self.session.headers.update(
            {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://kns.cnki.net",
                "Referer": "https://kns.cnki.net/kns8s/defaultresult/index",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if self.cookie:
            self._set_cookie_jar(parse_cookie_header(self.cookie))
        self.raw_article_ids = self.load_raw_article_ids()
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else self.output_dir / "checkpoint.json"
        self.reader_detail_available = True
        self.reader_references_available = True
        self.last_search_confirmed_empty = False
        self.last_search_complete = False
        self.blocked_reason = ""
        self.filtered_foreign_rows = 0

    @staticmethod
    def _validate_cnki_url(url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_CNKI_HOSTS
            or parsed.port not in {None, 443}
        ):
            raise ValueError(f"拒绝访问非 CNKI HTTPS 地址: {parsed.hostname or '<missing-host>'}")
        return url

    def _set_cookie_jar(self, parsed_cookie: dict[str, str]) -> None:
        self.session.cookies.clear()
        for name, value in parsed_cookie.items():
            self.session.cookies.set(name, value, domain=COOKIE_DOMAIN, secure=True)

    def reload_shard_cookie(self) -> bool:
        cookie = load_cookie_from_env(shard_id=self.shard_id) or load_cookie_from_file(self.env_path, shard_id=self.shard_id)
        parsed_cookie = parse_cookie_header(cookie)
        if not parsed_cookie:
            self.logger.error("shard=%s 无法从 %s 重新加载有效 Cookie", self.shard_id, self.env_path)
            return False
        self.cookie = cookie
        self._set_cookie_jar(parsed_cookie)
        self.logger.info("shard=%s 已从 %s 重新加载完整 Cookie", self.shard_id, self.env_path)
        return True

    def request_with_retry(self, method: str, url: str, **kwargs: Any):
        url = self._validate_cnki_url(url)
        allow_not_modified = kwargs.pop("_allow_not_modified", False)
        kwargs["allow_redirects"] = False
        response = super().request_with_retry(method, url, **kwargs)
        self._check_redirect_response(response, allow_not_modified)
        if response.status_code != 401 or self._classify_endpoint(url) in {"reader-detail", "reader-references"}:
            return response

        cookie_reloaded = self.reload_shard_cookie()
        if cookie_reloaded:
            response = super().request_with_retry(method, url, **kwargs)
            self._check_redirect_response(response, allow_not_modified)
        if response.status_code == 401:
            self.logger.warning(
                "shard=%s 刷新 Cookie 后仍收到 401，放弃当前请求并交由上层标记失败",
                self.shard_id,
            )
            if cookie_reloaded and self._classify_endpoint(url) == "brief-grid":
                raise CnkiCookieReplacementRequired("重新加载完整 Cookie 后搜索接口仍收到 401，需要人工更新 Cookie")
        return response

    @staticmethod
    def _check_redirect_response(response, allow_not_modified=False):
        if response.status_code == 304 and allow_not_modified:
            return
        if 300 <= response.status_code < 400:
            location = str(response.headers.get("Location", ""))
            if response.status_code != 304 and "verify" in location.lower():
                raise CnkiVerificationRequired("CNKI requires verification: /verify/home")
            raise ValueError(f"Rejected CNKI redirect: {response.status_code}")

    @staticmethod
    def _response_error_message(response) -> str:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("msg")
            if message:
                return str(message)
        return str(getattr(response, "text", ""))

    def observe_response(self, response, url: str) -> None:
        if response.status_code < 400:
            return
        endpoint = self._classify_endpoint(url)
        body = " ".join(str(getattr(response, "text", "")).split())
        body = re.sub(
            r"(?i)\bCookie\s*:\s*.*?(?=(?:\s+[A-Za-z][\w-]*\s*:)|$)",
            "Cookie:[REDACTED]",
            body,
        )
        body = re.sub(
            r'(?i)(["\'](?:cookie|authorization|(?:sid|session|token)[\w-]*)["\']\s*:\s*)["\'][^"\']*["\']',
            r'\1"[REDACTED]"',
            body,
        )
        body = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", body)
        body = re.sub(
            r"(?i)\b(cookie|authorization|(?:sid|session|token)[\w-]*)\s*([:=])\s*[^\s;,]+",
            r"\1\2[REDACTED]",
            body,
        )
        body = re.sub(r"https?://[^\s?'\"]+\?[^\s'\"]+", "[REDACTED_URL]", body)[:RESPONSE_LOG_LIMIT]
        self.logger.warning(
            "timestamp=%s shard=%s status=%s endpoint=%s body=%r",
            datetime.now().astimezone().isoformat(timespec="seconds"),
            self.shard_id,
            response.status_code,
            endpoint,
            body,
        )

    @staticmethod
    def _classify_endpoint(url: str) -> str:
        if "/quotations/metrics" in url:
            return "citation-metrics"
        if "/original-references-auth" in url:
            return "reader-references"
        if "/citation-api/v1/literature/" in url:
            return "citation-list"
        if "/brief/grid" in url:
            return "brief-grid"
        if "/literature-api/v1/articles/detail" in url:
            return "reader-detail"
        if "/defaultresult/index" in url:
            return "session-bootstrap"
        return "detail-page"

    def ensure_session(self) -> None:
        has_session_cookie = any(
            cookie.name == "SID_kns_new" and bool(cookie.value)
            for cookie in self.session.cookies
        )
        if not has_session_cookie:
            self.request_with_retry(
                "GET",
                "https://kns.cnki.net/kns8s/defaultresult/index",
                headers={
                    "User-Agent": self.session.headers.get("User-Agent", ""),
                    "Accept": "text/html",
                },
                timeout=10,
            )

    def refresh_session(self) -> None:
        for cookie in list(self.session.cookies):
            if cookie.name == "SID_kns_new":
                self.session.cookies.clear(cookie.domain, cookie.path, cookie.name)
        self.ensure_session()

    def _journal_override(self, journal_name: str) -> dict[str, Any]:
        return self.journal_overrides.get(normalize_journal_name(journal_name), {})

    def _journal_operator(self, journal_name: str) -> str:
        return str(self._journal_override(journal_name).get("operator") or DEFAULT_SOURCE_OPERATOR)

    def _journal_slice_strategy(self, journal_name: str) -> str:
        return str(self._journal_override(journal_name).get("slices") or "")

    def _expand_override_slices(self, journal_name: str, slices: list[str]) -> list[str]:
        """按月展开配置了 monthly 策略的期刊区间，其余期刊原样返回。"""
        if self._journal_slice_strategy(journal_name) != "monthly":
            return slices
        expanded: list[str] = []
        for value in slices:
            start, end = self._slice_date_bounds(value)
            cursor = start.replace(day=1)
            while cursor <= end:
                month_end = date(cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1])
                segment_end = min(month_end, end)
                expanded.append(f"{cursor.isoformat()}..{segment_end.isoformat()}")
                if month_end >= end:
                    break
                cursor = month_end + timedelta(days=1)
        return expanded

    def search_page(self, source_journal: str, page: int = 1, page_size: int = 20, year: str = "") -> tuple[list[dict[str, Any]], int]:
        self.ensure_session()
        safe_page_size = max(1, min(page_size, 50))
        payload = build_brief_grid_payload(
            source_journal=source_journal,
            classid="WD0FTY92",
            page_num=page,
            page_size=safe_page_size,
            year=year,
            operator=self._journal_operator(source_journal),
        )
        for attempt in range(self.max_retries + 1):
            response = self.request_with_retry(
                "POST",
                "https://kns.cnki.net/kns8s/brief/grid",
                data=payload,
                timeout=15,
            )
            if response.status_code != 200:
                raise ValueError(f"Unexpected status code: {response.status_code}")
            try:
                results, total = parse_brief_grid_response(response.text, source_journal)
                self.last_search_confirmed_empty = total == 0 and is_explicit_no_content_response(response.text)
                kept = [
                    article
                    for article in results
                    if journal_source_matches(
                        article.get("journal") or article.get("source") or "",
                        source_journal,
                    )
                ]
                dropped = len(results) - len(kept)
                if dropped:
                    self.filtered_foreign_rows += dropped
                    print(
                        f"  [过滤] {source_journal} 第{page}页 丢弃{dropped}篇来源不符的文章",
                        flush=True,
                    )
                kept, duplicate_rows = deduplicate_confirmed_source_rows(kept, source_journal)
                if duplicate_rows:
                    self.note_slice_truncation(
                        source_journal, year, total, len(kept), "confirmed-source-duplicate"
                    )
                return kept, total
            except RuntimeError as error:
                if attempt < self.max_retries:
                    print(
                        f"  [搜索重试] {error} ({attempt + 1}/{self.max_retries})...",
                        flush=True,
                    )
                    self.refresh_session()
                    continue
                raise

    def has_confirmed_source_duplicate(self, journal_name):
        """A persisted duplicate discrepancy keeps journal completion conservative."""
        path = self.output_dir / "truncated_slices.jsonl"
        if not path.exists():
            return False
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (record.get("journal") == journal_name
                        and record.get("reason") == "confirmed-source-duplicate"):
                    return True
        return False

    def audit_truncation_resolutions(self, journal_name: str) -> list[dict[str, Any]]:
        """Return blocking audit entries, not missing-article estimates.

        Historical entries may overlap or repeat. No resolution is automatically
        accepted, including source-filtered counts pending separate verification.
        The original audit file is never rewritten or cleared here.
        """
        path = self.output_dir / "truncated_slices.jsonl"
        if not path.exists():
            return []
        unresolved = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("journal") == journal_name:
                    unresolved.append({
                        **record,
                        "resolution": (
                            "source-filtered-unverified"
                            if record.get("reason") == "source-filtered"
                            else "unresolved"
                        ),
                    })
        return unresolved

    def note_slice_truncation(
        self,
        journal_name: str,
        slice_value: str,
        declared_total: int,
        taken: int,
        reason: str,
    ) -> None:
        """记录因放弃翻页/停止细分而未完整获取的切片，供后续补抓。"""
        record = {
            "journal": journal_name,
            "slice": slice_value,
            "declared_total": declared_total,
            "taken": taken,
            "reason": reason,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        print(
            f"  [截断] {journal_name} {slice_value} 声明{declared_total}篇，实取{taken}篇（{reason}）",
            flush=True,
        )
        try:
            self.append_jsonl(self.output_dir / "truncated_slices.jsonl", record)
        except Exception as error:  # noqa: BLE001
            self.logger.warning("无法写入截断记录：%s", error)

    @staticmethod
    def _slice_date_bounds(slice_value: str) -> tuple[date, date]:
        if ".." in slice_value:
            start_text, end_text = slice_value.split("..", 1)
            return date.fromisoformat(start_text), date.fromisoformat(end_text)
        if len(slice_value) == 4:
            year = int(slice_value)
            return date(year, 1, 1), date(year, 12, 31)
        raise ValueError(f"Unsupported adaptive date slice: {slice_value}")

    def _search_all_pages(
        self,
        journal_name: str,
        slice_value: str,
        page_size: int,
        first_page: tuple[list[dict[str, Any]], int] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        effective_page_size = max(1, min(page_size, 50))
        if first_page is None:
            first_page = self.search_page(
                journal_name,
                page=1,
                page_size=effective_page_size,
                year=slice_value,
            )
        expected_total = first_page[1]
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        page = 1
        current = first_page
        while True:
            results, total = current
            if total != expected_total:
                raise RuntimeError(
                    f"{journal_name} {slice_value} page {page} total changed: "
                    f"{expected_total} -> {total}"
                )
            page_ids = [build_article_id(article) for article in results]
            if len(page_ids) != len(set(page_ids)):
                raise RuntimeError(f"{journal_name} {slice_value} page {page} contains duplicate articles")
            duplicate_ids = seen_ids.intersection(page_ids)
            if duplicate_ids:
                raise RuntimeError(f"{journal_name} {slice_value} page {page} overlaps an earlier page")
            seen_ids.update(page_ids)
            collected.extend(results)
            if len(collected) >= expected_total:
                break
            if not results:
                raise RuntimeError(
                    f"{journal_name} {slice_value} ended after {len(collected)}/{expected_total} articles"
                )
            page += 1
            current = self.search_page(
                journal_name,
                page=page,
                page_size=effective_page_size,
                year=slice_value,
            )
        if len(collected) != expected_total:
            raise RuntimeError(
                f"{journal_name} {slice_value} returned {len(collected)}/{expected_total} articles"
            )
        return collected, expected_total

    def _iter_adaptive_date_slices(
        self,
        journal_name: str,
        slice_value: str,
        page_size: int,
        cached: tuple[list[dict[str, Any]], int] | None = None,
    ):
        effective_page_size = max(1, min(page_size, 50))
        if self._journal_slice_strategy(journal_name):
            results, total = cached or self.search_page(
                journal_name,
                page=1,
                page_size=effective_page_size,
                year=slice_value,
            )
            if total == 0 and results:
                raise RuntimeError(
                    f"{journal_name} {slice_value} 返回总数为 0，但解析到 {len(results)} 篇"
                )
            if total > effective_page_size:
                # 固定切片不做二分收敛，超出单页的部分只记录缺口。
                self.note_slice_truncation(
                    journal_name, slice_value, total, len(results), "fixed-slice-overflow"
                )
            elif len(results) != total:
                self.note_slice_truncation(
                    journal_name, slice_value, total, len(results), "source-filtered"
                )
            yield slice_value, results, len(results)
            return
        results, total = cached or self.search_page(
            journal_name,
            page=1,
            page_size=effective_page_size,
            year=slice_value,
        )
        if total <= effective_page_size:
            if total == 0:
                if results:
                    raise RuntimeError(
                        f"{journal_name} {slice_value} 返回总数为 0，但解析到 {len(results)} 篇"
                    )
                yield slice_value, results, total
                return
            if len(results) != total:
                # 来源过滤会丢掉模糊匹配到的其他刊物文章，以实际取得为准。
                effective_total = len(results)
                self.note_slice_truncation(
                    journal_name, slice_value, total, effective_total, "source-filtered"
                )
                if effective_total == 0:
                    yield slice_value, [], 0
                    return
                article_ids = [build_article_id(article) for article in results]
                if len(set(article_ids)) != len(article_ids):
                    raise RuntimeError(f"{journal_name} {slice_value} 第 1 页包含重复文章")
                yield slice_value, results, effective_total
                return
            article_ids = [build_article_id(article) for article in results]
            if len(set(article_ids)) != len(article_ids):
                raise RuntimeError(f"{journal_name} {slice_value} 第 1 页包含重复文章")
            yield slice_value, results, total
            return

        start, end = self._slice_date_bounds(slice_value)
        if start >= end:
            # 单日仍超过单页时不再翻页：翻页请求会触发 CNKI 验证码。
            # 稳定性优先，只保留第 1 页并记录缺口。
            self.note_slice_truncation(
                journal_name, slice_value, total, len(results), "single-day-overflow"
            )
            yield slice_value, results, len(results)
            return
        left_end = start + timedelta(days=(end - start).days // 2)
        right_start = left_end + timedelta(days=1)
        left_slice = f"{start.isoformat()}..{left_end.isoformat()}"
        right_slice = f"{right_start.isoformat()}..{end.isoformat()}"
        print(f"[切片] {journal_name} {slice_value} 共{total}篇，二分日期范围", flush=True)
        left = self.search_page(
            journal_name,
            page=1,
            page_size=effective_page_size,
            year=left_slice,
        )
        if left[1] >= total:
            # 区间缩小但总数没有下降，说明 CNKI 的模糊匹配把总数撑高了。
            self.note_slice_truncation(
                journal_name, slice_value, total, len(results), "non-convergent"
            )
            yield slice_value, results, len(results)
            return
        right = self.search_page(
            journal_name,
            page=1,
            page_size=effective_page_size,
            year=right_slice,
        )
        if left[1] + right[1] != total or right[1] >= total:
            # 计数不守恒同样说明总数不可靠，停止细分而不是让进程崩溃。
            self.note_slice_truncation(
                journal_name, slice_value, total, len(results), "count-not-conserved"
            )
            yield slice_value, results, len(results)
            return
        yield from self._iter_adaptive_date_slices(
            journal_name,
            left_slice,
            effective_page_size,
            cached=left,
        )
        yield from self._iter_adaptive_date_slices(
            journal_name,
            right_slice,
            effective_page_size,
            cached=right,
        )

    def search_one_journal(
        self,
        journal_name: str,
        page_size: int = 20,
        max_pages: int = 1,
        year_slices: list[str] | None = None,
        auto_detect_years: bool = False,
        year_end: int = 2026,
        year_start_floor: int = 1900,
        seen_article_ids: set[str] | None = None,
        journal_idx: int = 1,
        total_journals: int = 1,
        on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.cookie:
            raise ValueError("CNKI_COOKIE 未配置，无法访问需要登录权限的搜索接口。")

        results_for_journal: list[dict[str, Any]] = []
        self.last_search_complete = False
        self.last_search_confirmed_empty = False
        seen = seen_article_ids if seen_article_ids is not None else set()
        adaptive_slices: list[str] | None = None
        if auto_detect_years:
            if year_start_floor > year_end:
                raise ValueError("year_start_floor must not be after year_end")
            adaptive_slices = [f"{year_start_floor:04d}-01-01..{year_end:04d}-12-31"]
            print(
                f"[探测] {journal_name} 自适应覆盖 {year_start_floor}-01-01 至 {year_end}-12-31...",
                flush=True,
            )
        elif year_slices:
            adaptive_slices = year_slices

        if adaptive_slices is not None:
            adaptive_slices = self._expand_override_slices(journal_name, adaptive_slices)
            for root_slice in adaptive_slices:
                planned_slices = list(
                    self._iter_adaptive_date_slices(
                        journal_name,
                        root_slice,
                        page_size,
                    )
                )
                planned_ids = [
                    build_article_id(article)
                    for _, articles, _ in planned_slices
                    for article in articles
                ]
                if (
                    len(set(planned_ids)) != len(planned_ids)
                    and not self._journal_slice_strategy(journal_name)
                ):
                    raise RuntimeError(f"{journal_name} 叶区间存在重复文章")
                for slice_value, results, total in planned_slices:
                    new_results: list[dict[str, Any]] = []
                    for result in results:
                        article_id = build_article_id(result)
                        if article_id in seen:
                            continue
                        seen.add(article_id)
                        stored_result = {**result, "source_journal_input": journal_name}
                        if article_id not in self.raw_article_ids:
                            self.append_jsonl(self.output_dir / "raw.jsonl", stored_result)
                            self.raw_article_ids.add(article_id)
                        new_results.append(stored_result)
                    results_for_journal.extend(new_results)
                    print(
                        f"[搜索 {journal_idx}/{total_journals} {slice_value}] {journal_name} "
                        f"第1页 +{len(new_results)}篇 (总计{total}篇)",
                        flush=True,
                    )
                    if new_results and on_batch:
                        on_batch(new_results)
            self.last_search_complete = True
            return results_for_journal

        page = 1
        journal_article_count = 0
        journal_total = 0
        while max_pages <= 0 or page <= max_pages:
            results, total = self.search_page(
                journal_name,
                page=page,
                page_size=page_size,
            )
            if page == 1:
                journal_total = total
            new_results: list[dict[str, Any]] = []
            for result in results:
                article_id = build_article_id(result)
                if article_id in seen:
                    continue
                seen.add(article_id)
                stored_result = {**result, "source_journal_input": journal_name}
                if article_id not in self.raw_article_ids:
                    self.append_jsonl(self.output_dir / "raw.jsonl", stored_result)
                    self.raw_article_ids.add(article_id)
                new_results.append(stored_result)
            results_for_journal.extend(new_results)
            journal_article_count += len(results)
            print(
                f"[搜索 {journal_idx}/{total_journals}] {journal_name} 第{page}页 "
                f"+{len(new_results)}篇 (累计{journal_article_count}篇, 总计{journal_total}篇)",
                flush=True,
            )
            if new_results and on_batch:
                on_batch(new_results)
            if not results or journal_article_count >= journal_total:
                break
            page += 1
        self.last_search_complete = journal_article_count >= journal_total
        return results_for_journal

    def search_journals(
        self,
        journal_names: list[str],
        page_size: int = 20,
        max_pages: int = 1,
        year_slices: list[str] | None = None,
        auto_detect_years: bool = False,
        year_end: int = 2026,
        year_start_floor: int = 1900,
    ) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        seen_article_ids: set[str] = set()
        total_journals = len(journal_names)
        for journal_idx, journal_name in enumerate(journal_names, start=1):
            all_results.extend(
                self.search_one_journal(
                    journal_name,
                    page_size=page_size,
                    max_pages=max_pages,
                    year_slices=year_slices,
                    auto_detect_years=auto_detect_years,
                    year_end=year_end,
                    year_start_floor=year_start_floor,
                    seen_article_ids=seen_article_ids,
                    journal_idx=journal_idx,
                    total_journals=total_journals,
                )
            )
        print(f"[搜索完成] 共 {len(journal_names)} 本期刊, {len(all_results)} 篇文章", flush=True)
        return all_results

    def fetch_detail_page(self, article: dict[str, Any]) -> str:
        detail_url = self._validate_cnki_url(article.get("detail_url", ""))
        if not detail_url:
            raise ValueError("detail_url is required")
        response = self.request_with_retry(
            "GET",
            detail_url,
            headers={
                "User-Agent": self.session.headers.get("User-Agent", ""),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://kns.cnki.net/kns8s/defaultresult/index",
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise ValueError(f"Unexpected status code: {response.status_code}")
        return response.text

    def refresh_article_search_token(self, article: dict[str, Any]) -> bool:
        journal_name = str(article.get("source_journal_input") or article.get("journal") or "").strip()
        publication_time = str(article.get("publicationTime") or "")
        year = publication_time[:4] if re.fullmatch(r"\d{4}.*", publication_time) else ""
        if not journal_name:
            return False
        results, _ = self.search_page(
            journal_name,
            page=1,
            page_size=50,
            year=year,
        )
        target_id = build_article_id(article)
        refreshed = next(
            (result for result in results if build_article_id(result) == target_id),
            None,
        )
        if not refreshed or not refreshed.get("detail_url"):
            return False
        article["detail_url"] = refreshed["detail_url"]
        article.pop("detail_context", None)
        article.pop("reader_detail", None)
        return True

    def ensure_detail_context(self, article: dict[str, Any]) -> dict[str, Any]:
        detail_context = article.get("detail_context")
        if detail_context and detail_context.get("kcms_link"):
            return detail_context
        for attempt in range(2):
            try:
                detail_html = self.fetch_detail_page(article)
                return build_detail_context(article, detail_html)
            except CnkiVerificationRequired:
                if attempt == 0 and self.refresh_article_search_token(article):
                    continue
                raise
            except Exception as error:
                if attempt == 0 and self._is_auth_failure(error):
                    self.refresh_session()
                    article.pop("detail_context", None)
                    continue
                raise

    def fetch_citation_metrics(self, article: dict[str, Any]) -> dict[str, int]:
        detail_context = article.get("detail_context")
        if not detail_context:
            detail_context = build_detail_context(article)
        request_info = build_citation_metrics_request({**article, "detail_context": detail_context})
        for attempt in range(2):
            try:
                response = self.request_with_retry("GET", request_info["url"], headers=request_info["headers"], timeout=15)
                if response.status_code != 200:
                    raise ValueError(f"Unexpected status code: {response.status_code}")
                return parse_citation_metrics(response.json())
            except Exception as error:
                if attempt == 0 and self._is_auth_failure(error):
                    self.refresh_session()
                    article.pop("reader_detail", None)
                    article.pop("detail_context", None)
                    detail_context = self.ensure_detail_context(article)
                    request_info = build_citation_metrics_request({**article, "detail_context": detail_context})
                    continue
                raise

    def fetch_citation_list(self, article: dict[str, Any], relation_type: str = "references", file_type: str = REFERENCE_FILE_TYPE, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        detail_context = article.get("detail_context")
        if not detail_context:
            detail_context = build_detail_context(article)
        request_info = build_citation_list_request(
            {**article, "detail_context": detail_context},
            relation_type=relation_type,
            file_type=file_type,
            page=page,
            page_size=page_size,
        )
        response = None
        for attempt in range(2):
            try:
                response = self.request_with_retry("GET", request_info["url"], headers=request_info["headers"], timeout=15)
            except ValueError as error:
                if attempt == 0 and self._is_auth_failure(error):
                    print(f"  [引文重试] {relation_type}/{file_type} 第{page}页 session 过期，刷新会话后重试", flush=True)
                    self.refresh_session()
                    article.pop("detail_context", None)
                    detail_context = self.ensure_detail_context(article)
                    request_info = build_citation_list_request(
                        {**article, "detail_context": detail_context},
                        relation_type=relation_type,
                        file_type=file_type,
                        page=page,
                        page_size=page_size,
                    )
                    continue
                raise
            if response.status_code != 200:
                error = ValueError(f"Unexpected status code: {response.status_code}")
                if attempt == 0 and self._is_auth_failure(error):
                    print(f"  [引文重试] {relation_type}/{file_type} 第{page}页 session 过期，刷新会话后重试", flush=True)
                    self.refresh_session()
                    article.pop("detail_context", None)
                    detail_context = self.ensure_detail_context(article)
                    request_info = build_citation_list_request(
                        {**article, "detail_context": detail_context},
                        relation_type=relation_type,
                        file_type=file_type,
                        page=page,
                        page_size=page_size,
                    )
                    continue
                raise error
            break
        if response is None:
            raise ValueError("CNKI 引文接口刷新会话后仍未返回有效响应")
        return response.json()

    def fetch_reader_detail(self, article: dict[str, Any]) -> dict[str, Any]:
        request_info = build_reader_detail_request(article)
        response = self.request_with_retry(
            "POST",
            request_info["url"],
            headers=request_info["headers"],
            json=request_info["json"],
            timeout=15,
        )
        if response.status_code != 200:
            message = self._response_error_message(response)
            if response.status_code == 401 and "请求已过期" in message:
                self.reader_detail_available = False
                self.logger.warning("shard=%s reader-detail 参数已过期，本进程改用引文接口回退", self.shard_id)
            raise ValueError(f"Unexpected status code: {response.status_code}")
        payload = response.json()
        if payload.get("code", 0) not in (0, "0", None):
            raise ValueError(str(payload.get("message") or payload.get("msg") or f"CNKI reader detail error: {payload.get('code')}"))
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("CNKI reader 文章详情接口返回数据格式异常")
        return data

    def fetch_reader_references(self, article: dict[str, Any], page_size: int = 50, page: int = 1) -> dict[str, Any]:
        vid = extract_metadata_value(article.get("reader_detail", {}), "vid")
        if not vid:
            raise ValueError("reader vid is required")
        request_info = build_reader_references_request(vid, page=page, page_size=page_size)
        response = self.request_with_retry("GET", request_info["url"], headers=request_info["headers"], timeout=15)
        if response.status_code != 200:
            message = self._response_error_message(response)
            if response.status_code == 401 and "请求已过期" in message:
                self.reader_references_available = False
                self.logger.warning("shard=%s reader-references 参数已过期，本进程改用引文接口回退", self.shard_id)
            raise ValueError(f"Unexpected status code: {response.status_code}")
        return response.json()

    def _is_auth_failure(self, error: Exception) -> bool:
        message = str(error)
        return (
            "401" in message
            or "403" in message
            or "登录已过期" in message
            or "登录失效" in message
            or "验证码" in message
            or "Request failed after retries: 403" in message
        )

    def _refresh_reader_context(self, article: dict[str, Any]) -> dict[str, Any]:
        self.refresh_session()
        article.pop("reader_detail", None)
        article.pop("detail_context", None)
        return self.ensure_detail_context(article)

    def fetch_citation_relation_items(
        self,
        article: dict[str, Any],
        relation_type: str,
        file_type: str,
        page_size: int = 10,
        max_pages: int = 1,
    ) -> list[dict[str, Any]]:
        relation_items: list[dict[str, Any]] = []
        safe_page_size = max(1, min(page_size, 10))
        if relation_type == "references" and file_type == REFERENCE_FILE_TYPE and self.reader_references_available:
            for attempt in range(2):
                try:
                    detail_context = article.get("detail_context", {})
                    has_reader_context = bool(
                        detail_context.get("dbname")
                        or (not self.reader_detail_available and extract_metadata_value(detail_context, "vid"))
                    )
                    if not article.get("reader_detail") and has_reader_context:
                        if self.reader_detail_available:
                            try:
                                article["reader_detail"] = self.fetch_reader_detail(article)
                            except Exception:
                                if extract_metadata_value(article.get("detail_context", {}), "vid"):
                                    article["reader_detail"] = article.get("detail_context", {})
                                else:
                                    raise
                        elif extract_metadata_value(article.get("detail_context", {}), "vid"):
                            article["reader_detail"] = article.get("detail_context", {})
                    reader_page_size = 50
                    reader_page = 1
                    reader_total = 0
                    seen_reader_page_ids: set[str] = set()
                    while max_pages <= 0 or reader_page <= max_pages:
                        payload = self.fetch_reader_references(article, page_size=reader_page_size, page=reader_page)
                        if payload.get("code", 0) not in (0, "0", None):
                            raise ValueError(str(payload.get("message") or payload.get("msg") or f"CNKI reader references error: {payload.get('code')}"))
                        data = payload.get("data", {})
                        if not isinstance(data, dict):
                            raise ValueError("CNKI reader 参考文献接口返回数据格式异常")
                        raw_items = data.get("data", [])
                        normalized_items = normalize_relation_items(raw_items)
                        page_ids = {build_article_id(item) for item in normalized_items}
                        if page_ids & seen_reader_page_ids:
                            raise PartialCitationRelationError(
                                f"CNKI reader references 第{reader_page}页重复，停止分页",
                                relation_items,
                            )
                        seen_reader_page_ids.update(page_ids)
                        relation_items.extend(normalized_items)
                        total = int(data.get("total", 0) or 0)
                        reader_total = max(reader_total, total)
                        if not raw_items or len(raw_items) < reader_page_size:
                            if reader_total and len(relation_items) < reader_total:
                                raise PartialCitationRelationError(
                                    f"reader references incomplete: expected={reader_total} fetched={len(relation_items)}",
                                    relation_items,
                                )
                            break
                        if reader_total and reader_page * reader_page_size >= reader_total:
                            if len(relation_items) < reader_total:
                                raise PartialCitationRelationError(
                                    f"reader references incomplete: expected={reader_total} fetched={len(relation_items)}",
                                    relation_items,
                                )
                            break
                        if max_pages <= 0 and reader_page >= MAX_UNBOUNDED_RELATION_PAGES:
                            raise PartialCitationRelationError(
                                f"reader references exceeded {MAX_UNBOUNDED_RELATION_PAGES} pages",
                                relation_items,
                            )
                        reader_page += 1
                    if relation_items:
                        return relation_items
                    break
                except Exception as error:
                    if isinstance(error, PartialCitationRelationError):
                        raise
                    relation_items = []
                    if not self.reader_references_available:
                        break
                    if attempt == 0 and self._is_auth_failure(error):
                        try:
                            self._refresh_reader_context(article)
                        except Exception:
                            break
                        continue
                    break
        page = 1
        seen_page_ids: set[tuple[str, ...]] = set()
        while max_pages <= 0 or page <= max_pages:
            try:
                payload = self.fetch_citation_list(article, relation_type=relation_type, file_type=file_type, page=page, page_size=safe_page_size)
                if payload.get("code", 0) not in (0, "0", None):
                    raise ValueError(str(payload.get("message") or payload.get("msg") or f"CNKI API error: {payload.get('code')}"))
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    raise ValueError("CNKI 引文接口返回数据格式异常")
                raw_items = data.get("data", [])
                normalized_items = normalize_relation_items(raw_items)
                page_ids = {relation_item_identity(item) for item in normalized_items}
                if page_ids and page_ids <= seen_page_ids:
                    if relation_items:
                        raise PartialCitationRelationError(
                            f"CNKI {relation_type}/{file_type} 第{page}页重复，停止分页",
                            relation_items,
                        )
                    break
                seen_page_ids.update(page_ids)
                relation_items.extend(normalized_items)
                total = int(data.get("total", 0) or 0)
            except ValueError as error:
                if relation_items:
                    raise PartialCitationRelationError(str(error), relation_items) from error
                raise
            if not raw_items or len(raw_items) < safe_page_size:
                break
            if total and page * safe_page_size >= total:
                break
            if max_pages <= 0 and page >= MAX_UNBOUNDED_RELATION_PAGES:
                raise PartialCitationRelationError(
                    f"CNKI {relation_type}/{file_type} 超过 {MAX_UNBOUNDED_RELATION_PAGES} 页",
                    relation_items,
                )
            page += 1
        return relation_items

    def enrich_articles(self, articles: list[dict[str, Any]], relation_page_size: int = 10, max_relation_pages: int = 0) -> list[dict[str, Any]]:
        enriched_articles: list[dict[str, Any]] = []
        total_articles = len(articles)
        for idx, article in enumerate(articles, start=1):
            title = article.get("title", "")[:40]
            enriched_article = {
                **article,
                "article_id": build_article_id(article),
                "detail_urls": build_detail_urls(article),
                "detail_context": article.get("detail_context", {}),
                "citation_network_status": article.get("citation_network_status", "pending"),
            }
            try:
                enriched_article["detail_context"] = self.ensure_detail_context(enriched_article)
            except CnkiVerificationRequired:
                raise
            except Exception as error:
                enriched_article["citation_network_status"] = "failed"
                enriched_article["citation_network_error"] = sanitize_error_message(error)
                for relation_field in RELATION_FIELDS:
                    enriched_article[relation_field] = list(article.get(relation_field, []))
                enriched_articles.append(enriched_article)
                continue
            detail_abstract = enriched_article.get("detail_context", {}).get("abstract", "")
            if not enriched_article.get("abstract") and detail_abstract:
                enriched_article["abstract"] = detail_abstract
            for field_name in DETAIL_METADATA_FIELDS:
                detail_value = enriched_article.get("detail_context", {}).get(field_name, "")
                if not enriched_article.get(field_name) and detail_value:
                    enriched_article[field_name] = detail_value
            for relation_field in RELATION_FIELDS:
                enriched_article[relation_field] = list(article.get(relation_field, []))

            relation_errors: list[str] = []
            relation_metrics: dict[str, int] = {}
            try:
                relation_metrics = self.fetch_citation_metrics(enriched_article)
                enriched_article["citation_metrics"] = relation_metrics
            except CnkiVerificationRequired:
                raise
            except Exception as error:
                relation_errors.append(sanitize_error_message(error))
                enriched_article["citation_metrics"] = {}
                relation_metrics = {field_name: 1 for field_name in RELATION_TYPE_FIELD_MAP.values()}

            active_relations = 0
            successful_relations = 0
            had_any_error = bool(relation_errors)
            for relation_type in DETAIL_RELATION_TYPES:
                relation_field = RELATION_TYPE_FIELD_MAP[relation_type]
                relation_items: list[dict[str, Any]] = []
                relation_had_error = False
                reference_partial_error = False
                relation_error_start = len(relation_errors)
                citation_page_succeeded = False
                relation_expected_count = relation_metrics.get(relation_field, 0)
                if relation_expected_count <= 0:
                    enriched_article[relation_field] = relation_items
                    continue
                active_relations += 1
                file_types = CITATION_FILE_TYPES if relation_type == "citations" else FALLBACK_RELATION_FILE_TYPES
                for file_type in file_types:
                    print(f"    [关系] {relation_type}/{file_type} 开始", flush=True)
                    try:
                        relation_items.extend(
                            self.fetch_citation_relation_items(
                                enriched_article,
                                relation_type=relation_type,
                                file_type=file_type,
                                page_size=relation_page_size,
                                max_pages=max_relation_pages,
                            )
                        )
                        if relation_type == "citations":
                            citation_page_succeeded = True
                    except CnkiVerificationRequired:
                        raise
                    except PartialCitationRelationError as error:
                        relation_items.extend(error.items)
                        reference_partial_error = relation_type == 'references'
                        relation_had_error = True
                        had_any_error = True
                        relation_errors.append(sanitize_error_message(error))
                        continue
                    except Exception as error:
                        relation_had_error = True
                        had_any_error = True
                        relation_errors.append(sanitize_error_message(error))
                        continue
                if (relation_type == "references" and (not relation_had_error or reference_partial_error)
                        and len(relation_items) < relation_expected_count and max_relation_pages <= 0):
                    # The reader can expose only a subset of source databases.
                    api_items = []
                    api_ok = True
                    reader_available = self.reader_references_available
                    self.reader_references_available = False
                    try:
                        for file_type in CITATION_FILE_TYPES:
                            try:
                                api_items.extend(self.fetch_citation_relation_items(
                                    enriched_article, relation_type="references", file_type=file_type,
                                    page_size=relation_page_size, max_pages=max_relation_pages,
                                ))
                            except CnkiVerificationRequired:
                                raise
                            except Exception:
                                api_ok = False
                                break
                    finally:
                        self.reader_references_available = reader_available
                    unique = {}
                    for item in api_items:
                        filename = item.get('file_name')
                        if not filename:
                            api_ok = False  # Ambiguous identity cannot prove completeness.
                            break
                        key = (filename, item.get('db_name') or '')
                        unique.setdefault(key, item)
                    api_items = list(unique.values())
                    if api_ok and len(api_items) == relation_expected_count:
                        relation_items = api_items
                        if reference_partial_error:
                            del relation_errors[relation_error_start:]
                            relation_had_error = False
                            had_any_error = bool(relation_errors)
                if relation_type == "citations":
                    relation_items = deduplicate_relation_items(relation_items)
                    if len(relation_items) != relation_expected_count:
                        relation_had_error = True
                        had_any_error = True
                        relation_errors.append(
                            f"citing_articles incomplete: expected={relation_expected_count} fetched={len(relation_items)}"
                        )
                elif len(relation_items) < relation_expected_count:
                    relation_had_error = True
                    had_any_error = True
                    relation_errors.append(
                        f"references incomplete: expected={relation_expected_count} fetched={len(relation_items)}"
                    )
                enriched_article[relation_field] = relation_items
                if relation_items or (relation_type == "citations" and citation_page_succeeded):
                    successful_relations += 1
                if relation_had_error and not relation_items:
                    had_any_error = True

            if successful_relations == active_relations and not had_any_error:
                enriched_article["citation_network_status"] = "success"
                enriched_article["citation_network_error"] = ""
            elif successful_relations > 0:
                enriched_article["citation_network_status"] = "partial"
                enriched_article["citation_network_error"] = relation_errors[0] if relation_errors else ""
            else:
                enriched_article["citation_network_status"] = "failed"
                enriched_article["citation_network_error"] = relation_errors[0] if relation_errors else ""

            enriched_articles.append(enriched_article)
            refs = len(enriched_article.get("references", []))
            cites = len(enriched_article.get("citing_articles", []))
            print(f"  [{idx}/{total_articles}] {title}... 状态={enriched_article['citation_network_status']} 引用={refs} 被引={cites}", flush=True)
        return enriched_articles

    def write_enriched_jsonl(self, articles: list[dict[str, Any]]) -> None:
        output_path = self.output_dir / "enriched.jsonl"
        with output_path.open("w", encoding="utf-8") as file:
            for article in articles:
                file.write(json.dumps(article, ensure_ascii=False) + "\n")

    def compact_jsonl(self, path: str | Path) -> None:
        jsonl_path = Path(path)
        if not jsonl_path.exists():
            return
        index_path = jsonl_path.with_suffix(jsonl_path.suffix + ".index.sqlite")
        temp_path = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")
        connection = sqlite3.connect(index_path)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS latest (article_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("DELETE FROM latest")
            with jsonl_path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    article_id = item.get("article_id") or build_article_id(item)
                    connection.execute(
                        "INSERT OR REPLACE INTO latest(article_id, payload) VALUES (?, ?)",
                        (article_id, json.dumps(item, ensure_ascii=False)),
                    )
            connection.commit()
            with temp_path.open("w", encoding="utf-8") as file:
                for (payload,) in connection.execute("SELECT payload FROM latest ORDER BY rowid"):
                    file.write(payload + "\n")
        finally:
            connection.close()
            index_path.unlink(missing_ok=True)
        temp_path.replace(jsonl_path)

    def load_raw_article_ids(self) -> set[str]:
        output_path = self.output_dir / "raw.jsonl"
        if not output_path.exists():
            return set()
        article_ids: set[str] = set()
        with output_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                article = json.loads(line)
                article_ids.add(article.get("article_id") or build_article_id(article))
        return article_ids

    def load_enriched_articles_by_id(self) -> dict[str, dict[str, Any]]:
        output_path = self.output_dir / "enriched.jsonl"
        if not output_path.exists():
            return {}
        articles_by_id: dict[str, dict[str, Any]] = {}
        with output_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                article = json.loads(line)
                article_id = article.get("article_id") or build_article_id(article)
                articles_by_id[article_id] = article
        return articles_by_id

    def load_enriched_statuses(self) -> dict[str, str]:
        output_path = self.output_dir / "enriched.jsonl"
        if not output_path.exists():
            return {}
        statuses: dict[str, str] = {}
        with output_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                article = json.loads(line)
                article_id = article.get("article_id") or build_article_id(article)
                statuses[article_id] = str(article.get("citation_network_status", ""))
        return statuses

    def load_completed_enriched_articles(self) -> dict[str, dict[str, Any]]:
        return {
            article_id: {"citation_network_status": status}
            for article_id, status in self.load_enriched_statuses().items()
            if status == "success"
        }

    def process_journal(
        self,
        journal_name: str,
        completed_articles: dict[str, dict[str, Any]],
        enriched_by_id: dict[str, dict[str, Any]],
        page_size: int = 20,
        max_pages: int = 1,
        relation_page_size: int = 10,
        max_relation_pages: int = 0,
        limit_articles: int = 0,
        resume: bool = True,
        year_slices: list[str] | None = None,
        auto_detect_years: bool = False,
        year_end: int = 2026,
        year_start_floor: int = 1900,
        seen_article_ids: set[str] | None = None,
        journal_idx: int = 1,
        total_journals: int = 1,
        collect_results: bool = True,
    ) -> list[dict[str, Any]]:
        output_path = self.output_dir / "enriched.jsonl"
        journal_article_count = 0
        batch_mode_used = False

        def enrich_batch(batch_articles: list[dict[str, Any]]) -> None:
            nonlocal journal_article_count, batch_mode_used
            batch_mode_used = True
            if limit_articles > 0:
                remaining = limit_articles - journal_article_count
                if remaining <= 0:
                    return
                batch_articles = batch_articles[:remaining]
            journal_article_count += len(batch_articles)
            pending_articles = [article for article in batch_articles if build_article_id(article) not in completed_articles]
            if not pending_articles:
                return
            print(f"[增强阶段] {journal_name} 切片共 {len(pending_articles)} 篇待处理", flush=True)
            for article in pending_articles:
                enriched_article = self.enrich_articles(
                    [article],
                    relation_page_size=relation_page_size,
                    max_relation_pages=max_relation_pages,
                )[0]
                article_id = enriched_article.get("article_id") or build_article_id(article)
                if collect_results:
                    enriched_by_id[article_id] = enriched_article
                if enriched_article.get("citation_network_status") == "success":
                    completed_articles[article_id] = {"citation_network_status": "success"}
                if resume:
                    self.append_jsonl(output_path, enriched_article)
                    export_article_incrementally(enriched_article, self.output_dir)

        articles = self.search_one_journal(
            journal_name,
            page_size=page_size,
            max_pages=max_pages,
            year_slices=year_slices,
            auto_detect_years=auto_detect_years,
            year_end=year_end,
            year_start_floor=year_start_floor,
            seen_article_ids=seen_article_ids,
            journal_idx=journal_idx,
            total_journals=total_journals,
            on_batch=enrich_batch,
        )
        if limit_articles > 0:
            articles = articles[:limit_articles]
        if not batch_mode_used:
            pending_articles = [article for article in articles if build_article_id(article) not in completed_articles]
            print(f"[增强阶段] {journal_name} 共 {len(articles)} 篇文章, 待处理 {len(pending_articles)} 篇", flush=True)
            for article in pending_articles:
                enriched_article = self.enrich_articles(
                    [article],
                    relation_page_size=relation_page_size,
                    max_relation_pages=max_relation_pages,
                )[0]
                article_id = enriched_article.get("article_id") or build_article_id(article)
                if collect_results:
                    enriched_by_id[article_id] = enriched_article
                if enriched_article.get("citation_network_status") == "success":
                    completed_articles[article_id] = {"citation_network_status": "success"}
                if resume:
                    self.append_jsonl(output_path, enriched_article)
                    export_article_incrementally(enriched_article, self.output_dir)
        if collect_results:
            export_all_formats(list(enriched_by_id.values()), self.output_dir)
        elif resume:
            self.compact_jsonl(output_path)
            self.compact_jsonl(self.output_dir / "articles.jsonl")
            export_all_formats_from_jsonl(output_path, self.output_dir)
        return articles

    def _cookie_fingerprint(self) -> str:
        key_path = self.cache_dir / ".cookie-alert-key"
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = token_bytes(32)
            key_path.write_bytes(key)
        return new_hmac(key, self.cookie.encode("utf-8"), sha256).hexdigest()

    def run(
        self,
        journal_names: list[str],
        page_size: int = 20,
        max_pages: int = 1,
        relation_page_size: int = 10,
        max_relation_pages: int = 0,
        limit_articles: int = 0,
        resume: bool = True,
        year_slices: list[str] | None = None,
        auto_detect_years: bool = False,
        year_end: int = 2026,
        year_start_floor: int = 1900,
        collect_results: bool = True,
    ) -> list[dict[str, Any]]:
        completed_articles = self.load_completed_enriched_articles() if resume else {}
        enriched_by_id: dict[str, dict[str, Any]] = (
            self.load_enriched_articles_by_id() if resume and collect_results else {}
        )
        checkpoint_exists = self.checkpoint_path.exists()
        persisted_checkpoint = load_checkpoint(self.checkpoint_path)
        if persisted_checkpoint.get("blocked_reason") == "verification_required":
            self.blocked_reason = "verification_required"
            return []
        checkpoint = persisted_checkpoint if resume else empty_checkpoint()
        if resume and checkpoint_exists and checkpoint.get("shard_count") != self.shard_count:
            raise RuntimeError(
                f"checkpoint shard_count={checkpoint.get('shard_count')} 与当前 shard_count={self.shard_count} 不兼容"
            )
        if resume and checkpoint_exists and checkpoint.get("shard_id") != self.shard_id:
            raise RuntimeError(
                f"checkpoint shard_id={checkpoint.get('shard_id')} 与当前 shard_id={self.shard_id} 不兼容"
            )
        checkpoint = {
            **checkpoint,
            "shard_id": self.shard_id,
            "shard_count": self.shard_count,
        }
        if checkpoint.get("blocked_reason") == "verification_required":
            self.blocked_reason = "verification_required"
            return []
        completed_journals = set(checkpoint.get("completed_journals", [])) if resume else set()
        seen_article_ids: set[str] = set()
        total_journals = len(journal_names)
        for journal_idx, journal_name in enumerate(journal_names, start=1):
            if self.blocked_reason:
                break
            if resume and journal_name in completed_journals:
                print(f"[跳过] {journal_name} 已在 checkpoint 中完成", flush=True)
                continue
            checkpoint = stamp_checkpoint({
                **checkpoint,
                "current_journal": journal_name,
                "blocked_reason": "",
                "blocked_message": "",
            })
            if resume:
                save_checkpoint(self.checkpoint_path, checkpoint)
            self.last_search_confirmed_empty = False
            self.last_search_complete = False
            try:
                journal_articles = self.process_journal(
                    journal_name,
                    completed_articles=completed_articles,
                    enriched_by_id=enriched_by_id,
                    page_size=page_size,
                    max_pages=max_pages,
                    relation_page_size=relation_page_size,
                    max_relation_pages=max_relation_pages,
                    limit_articles=limit_articles,
                    resume=resume,
                    year_slices=year_slices,
                    auto_detect_years=auto_detect_years,
                    year_end=year_end,
                    year_start_floor=year_start_floor,
                    seen_article_ids=seen_article_ids,
                    journal_idx=journal_idx,
                    total_journals=total_journals,
                    collect_results=collect_results,
                )
            except CnkiCookieReplacementRequired as error:
                self.blocked_reason = "cookie_replacement_required"
                cookie_fingerprint = self._cookie_fingerprint()
                already_notified = (
                    bool(checkpoint.get("cookie_replacement_notified"))
                    and checkpoint.get("cookie_replacement_cookie_fingerprint") == cookie_fingerprint
                )
                notified = already_notified
                if not already_notified:
                    try:
                        notified = send_cookie_replacement_email(
                            shard_id=self.shard_id,
                            reason=sanitize_error_message(error),
                            env_path=self.env_path,
                        )
                    except Exception as notify_error:
                        self.logger.error(
                            "shard=%s Cookie 更新通知邮件发送失败：%s",
                            self.shard_id,
                            sanitize_error_message(notify_error),
                        )
                checkpoint = stamp_checkpoint({
                    **checkpoint,
                    "current_journal": journal_name,
                    "blocked_reason": self.blocked_reason,
                    "blocked_message": sanitize_error_message(error),
                    "cookie_replacement_notified": notified,
                    "cookie_replacement_cookie_fingerprint": cookie_fingerprint,
                })
                if resume:
                    save_checkpoint(self.checkpoint_path, checkpoint)
                self.logger.error(
                    "shard=%s 完整 Cookie 已确认失效，暂停运行并等待人工更新",
                    self.shard_id,
                )
                break
            except CnkiVerificationRequired as error:
                self.blocked_reason = "verification_required"
                checkpoint = stamp_checkpoint({
                    **checkpoint,
                    "current_journal": journal_name,
                    "blocked_reason": self.blocked_reason,
                    "blocked_message": sanitize_error_message(error),
                    "verification_probe_result": "",
                    "verification_probe_delay_minutes": 15,
                    "verification_probe_next_at": (
                        datetime.now().astimezone() + timedelta(minutes=15)
                    ).isoformat(),
                })
                if resume:
                    save_checkpoint(self.checkpoint_path, checkpoint)
                self.logger.error(
                    "shard=%s 检测到 CNKI 验证页，暂停当前运行；更新 Cookie 后再恢复：%s",
                    self.shard_id,
                    error,
                )
                break
            journal_article_ids = [build_article_id(article) for article in journal_articles]
            journal_complete = (
                limit_articles <= 0
                and self.last_search_complete
                and not self.has_confirmed_source_duplicate(journal_name)
                and not self.audit_truncation_resolutions(journal_name)
                and (
                    (bool(journal_article_ids) and all(
                        article_id in completed_articles
                        for article_id in journal_article_ids
                    ))
                    or (not journal_article_ids and self.last_search_confirmed_empty)
                )
            )
            if journal_complete:
                completed_journals.add(journal_name)
            checkpoint = stamp_checkpoint({
                **checkpoint,
                "current_journal": "",
                "completed_journals": sorted(completed_journals),
            })
            if resume:
                save_checkpoint(self.checkpoint_path, checkpoint)
        enriched_articles = list(enriched_by_id.values()) if collect_results else []
        if resume:
            self.compact_jsonl(self.output_dir / "raw.jsonl")
            self.compact_jsonl(self.output_dir / "enriched.jsonl")
            self.compact_jsonl(self.output_dir / "articles.jsonl")
            export_all_formats_from_jsonl(self.output_dir / "enriched.jsonl", self.output_dir)
        else:
            self.write_enriched_jsonl(enriched_articles)
            export_all_formats(enriched_articles, self.output_dir)
        return enriched_articles
