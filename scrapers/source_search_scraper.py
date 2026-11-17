from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date
from typing import Any

from bs4 import BeautifulSoup


KUAKU_CODES = "YSTT4HG0,LSTPFY1C,JUP3MUPD,MPMFIG1A,EMRPGLPA,WQ0UVIAA,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R"
PRODUCT_STR = "YSTT4HG0,LSTPFY1C,RMJLXHZ3,JQIRZIYA,JUP3MUPD,1UR4K4HZ,BPBAFJ5S,R79MZMCB,MPMFIG1A,EMRPGLPA,J708GVCE,ML4DRIDX,WQ0UVIAA,NB3BWEHK,XVLO76FD,HR1YT1Z9,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R,"



def _resolve_date_range(
    year: str,
    date_start: str = "",
    date_end: str = "",
) -> tuple[str, str] | None:
    if date_start or date_end:
        if not date_start or not date_end:
            raise ValueError("date_start and date_end must be provided together")
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if start > end:
            raise ValueError("date_start must not be after date_end")
        return start.isoformat(), end.isoformat()
    if not year:
        return None
    if ".." in year:
        start_text, end_text = year.split("..", 1)
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        if start > end:
            raise ValueError("year date range must be ordered")
        return start.isoformat(), end.isoformat()
    if len(year) == 4:
        return f"{year}-01-01", f"{year}-12-31"
    year_number, month_number = (int(value) for value in year.split("-", 1))
    month_start = date(year_number, month_number, 1)
    month_end = date(year_number, month_number, monthrange(year_number, month_number)[1])
    return month_start.isoformat(), month_end.isoformat()



def build_brief_grid_payload(
    source_journal: str,
    classid: str,
    resource: str = "CROSSDB",
    page_num: int = 1,
    page_size: int = 20,
    sort_field: str = "",
    sort_type: str = "",
    year: str = "",
    date_start: str = "",
    date_end: str = "",
    operator: str = "FUZZY",
) -> dict[str, Any]:
    items = [
        {
            "Field": "LY",
            "Value": source_journal,
            "Operator": operator,
            "Logic": 0,
            "Title": "文献来源",
        }
    ]
    query_groups = [
        {
            "Key": "Subject",
            "Title": "",
            "Logic": 0,
            "Items": items,
            "ChildItems": [],
        }
    ]
    resolved_range = _resolve_date_range(year, date_start=date_start, date_end=date_end)
    if resolved_range:
        year_start, year_end = resolved_range
        query_groups.append(
            {
                "Key": "ControlGroup",
                "Title": "",
                "Logic": 0,
                "Items": [
                    {
                        "Key": "span[value=PT]",
                        "Title": "发表时间",
                        "Logic": 0,
                        "Field": "PT",
                        "Operator": 7,
                        "Value": year_start,
                        "Value2": year_end,
                    }
                ],
                "ChildItems": [],
            }
        )
    query_json = {
        "Platform": "",
        "Resource": resource,
        "Classid": classid,
        "Products": "",
        "QNode": {
            "QGroup": query_groups
        },
        "ExScope": 1,
        "SearchType": 2,
        "Rlang": "CHINESE",
        "KuaKuCode": KUAKU_CODES,
        "Expands": {},
        "View": "changeDBCh",
        "SearchFrom": 1,
    }
    return {
        "boolSearch": True,
        "QueryJson": json.dumps(query_json, ensure_ascii=False),
        "pageNum": page_num,
        "pageSize": page_size,
        "sortField": sort_field,
        "sortType": sort_type,
        "dstyle": "listmode",
        "boolSortSearch": False,
        "sentenceSearch": False,
        "productStr": PRODUCT_STR,
        "aside": f"(文献来源：{source_journal})",
        "searchFrom": "资源范围：总库",
        "subject": "",
        "language": "",
        "uniplatform": "",
        "CurPage": str(page_num),
    }



def build_source_search_body(
    source_journal: str,
    page: int = 1,
    page_size: int = 20,
    date_start: str = "",
    date_end: str = "",
) -> dict[str, Any]:
    return build_brief_grid_payload(
        source_journal=source_journal,
        classid="WD0FTY92",
        page_num=page,
        page_size=page_size,
        date_start=date_start,
        date_end=date_end,
    )



def is_explicit_no_content_response(html: str) -> bool:
    if not html.strip():
        return False
    soup = BeautifulSoup(html, "lxml")
    empty_node = soup.select_one("#briefBox .no-content")
    if empty_node is None or soup.select_one("table.result-table-list tbody tr"):
        return False
    message = empty_node.get_text(" ", strip=True)
    return any(marker in message for marker in ("没有检索到", "暂无数据", "无检索结果"))



def parse_brief_grid_response(html: str, source_journal: str) -> tuple[list[dict[str, Any]], int]:
    if "vericodeForm" in html or "请输入验证码" in html:
        raise RuntimeError("CNKI 返回验证码页面，需要刷新会话后重试")
    if not html.strip():
        raise RuntimeError("CNKI 返回空响应，需要刷新会话后重试")
    soup = BeautifulSoup(html, "lxml")
    if is_explicit_no_content_response(html):
        return [], 0
    has_result_container = bool(
        soup.select_one("#countPageDiv")
        or soup.select_one("table.result-table-list")
    )
    if not has_result_container:
        raise RuntimeError("CNKI 返回结构异常，需要刷新会话后重试")

    total = 0
    pager_total = soup.select_one("#countPageDiv .pagerTitleCell em")
    if pager_total:
        total = int(re.sub(r"[^\d]", "", pager_total.get_text()) or "0")

    results: list[dict[str, Any]] = []
    for row in soup.select("table.result-table-list tbody tr"):
        title_node = row.select_one("td.name a.fz14")
        if title_node is None:
            continue

        author_nodes = row.select("td.author a")
        source_node = row.select_one("td.source")
        date_node = row.select_one("td.date")
        db_type_node = row.select_one("td.data span")
        quote_node = row.select_one("td.quote")
        download_node = row.select_one("a.downloadCnt")
        collect_node = row.select_one("a.icon-collect")

        quote_frequency = quote_node.get_text(" ", strip=True) if quote_node else ""
        download_count = download_node.get_text(strip=True) if download_node else ""

        results.append(
            {
                "source": "cnki",
                "title": title_node.get_text(strip=True),
                "authors": ";".join(node.get_text(strip=True) for node in author_nodes if node.get_text(strip=True)),
                "journal": source_node.get_text(" ", strip=True) if source_node else "",
                "publicationTime": date_node.get_text(strip=True) if date_node else "",
                "abstract": "",
                "quote_frequency": quote_frequency,
                "download_count": download_count,
                "file_name": collect_node.get("data-filename", "") if collect_node else "",
                "db_name": collect_node.get("data-dbname", "") if collect_node else "",
                "db_type": collect_node.get("data-resource", "") if collect_node else (db_type_node.get_text(strip=True) if db_type_node else ""),
                "detail_url": title_node.get("href", ""),
                "source_journal_input": source_journal,
                "search_mode": "source",
                "citation_network_status": "pending",
            }
        )

    return results, total


def fetch_search_page(
    session: Any,
    api_url: str,
    source_journal: str,
    page: int = 1,
    page_size: int = 20,
    timeout: int = 15,
) -> tuple[list[dict[str, Any]], int]:
    body = build_source_search_body(source_journal, page=page, page_size=page_size)
    if hasattr(session, "request_with_retry"):
        response = session.request_with_retry("POST", api_url, data=body, timeout=timeout)
    else:
        response = session.post(api_url, data=body, timeout=timeout)
    if response.status_code != 200:
        raise ValueError(f"Unexpected status code: {response.status_code}")

    return parse_brief_grid_response(response.text, source_journal)



def parse_search_result_item(item: dict[str, Any], source_journal: str) -> dict[str, Any]:
    return {
        "source": "cnki",
        "title": item.get("title", ""),
        "authors": item.get("author", ""),
        "journal": item.get("publishName", ""),
        "publicationTime": item.get("publicationTime", ""),
        "abstract": item.get("summary", ""),
        "quote_frequency": item.get("quoteFrequency", ""),
        "download_count": item.get("downloadCount", ""),
        "file_name": item.get("fileName", ""),
        "db_name": item.get("dBName", ""),
        "db_type": item.get("type", ""),
        "source_journal_input": source_journal,
        "search_mode": "source",
        "citation_network_status": "pending",
    }
