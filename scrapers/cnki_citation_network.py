from __future__ import annotations

import hashlib
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote

from bs4 import BeautifulSoup


CITATION_RELATION_TYPES = (
    "references",
    "citations",
    "coreferences",
    "cocitations",
    "secondreferences",
    "secondcitations",
)

RELATION_TYPE_FIELD_MAP = {
    "references": "references",
    "citations": "citing_articles",
    "coreferences": "co_cited_articles",
    "cocitations": "co_citing_articles",
    "secondreferences": "secondary_references",
    "secondcitations": "secondary_citing_articles",
}

RELATION_API_PATH_MAP = {
    "references": "references",
    "citations": "citations",
    "coreferences": "co-references",
    "cocitations": "co-citations",
    "secondreferences": "second-references",
    "secondcitations": "second-citations",
}

RELATION_FILE_TYPES = (
    "CJFDREF",
    "CDFDREF",
    "CMFDREF",
    "CPFDREF",
    "IPFDREF",
    "CYFDREF",
    "CCNDREF",
    "WWJDREF",
    "CBBDREF",
    "SCPDREF",
    "SCSDREF",
    "NLNKREF",
)

REFERENCE_FILE_TYPE = "CJFDREF"

READER_DETAIL_VV = "ea22d164e7e3967fd6666b0046c5e5dcb087e4d7058e773f5832183aa359a163"
READER_CLIENT_ID = "d60046d3-2c5f-4be9-8d68-47fcf754a0b0"

DETAIL_METADATA_LABELS = {
    "institution": ("作者单位", "单位"),
    "keywords": ("关键词", "关键字"),
    "funding": ("基金资助", "基金"),
    "album": ("专辑",),
    "special_topic": ("专题",),
    "classification_number": ("分类号",),
}
ALL_DETAIL_METADATA_LABELS = tuple(label for labels in DETAIL_METADATA_LABELS.values() for label in labels)



def build_article_id(article: dict[str, Any]) -> str:
    file_name = article.get("file_name", "")
    db_name = article.get("db_name", "")
    if file_name and db_name:
        return f"{file_name}:{db_name}"
    title = article.get("title", "").strip()
    return hashlib.md5(title.encode("utf-8")).hexdigest()



def build_detail_urls(article: dict[str, Any]) -> dict[str, str]:
    file_name = article.get("file_name", "")
    db_name = article.get("db_name", "")
    if not file_name or not db_name:
        return {}

    encoded_file_name = quote(file_name)
    encoded_db_name = quote(db_name)
    return {
        "kns": f"https://kns.cnki.net/kcms/detail/detail.aspx?filename={encoded_file_name}&dbcode={encoded_db_name}",
        "html": f"https://wap.cnki.net/touch/web/article/detail?filename={encoded_file_name}&dbcode={encoded_db_name}",
        "download": f"https://wap.cnki.net/touch/web/article/download?filename={encoded_file_name}&dbcode={encoded_db_name}",
    }



def build_detail_context(article: dict[str, Any], html: str | None = None) -> dict[str, str]:
    context = {
        "api_host": "https://kns.cnki.net/restapi",
        "kcms_link": "",
        "filename": article.get("file_name", ""),
        "dbcode": article.get("db_name", ""),
        "dbname": article.get("dbname", article.get("db_name", "")),
        "resource": article.get("db_type", article.get("resource", "")),
        "abstract": article.get("abstract", ""),
        **{field_name: article.get(field_name, "") for field_name in DETAIL_METADATA_LABELS},
    }
    if html:
        soup = BeautifulSoup(html, "lxml")
        api_host_node = soup.select_one("#apiHost")
        kcms_node = soup.select_one("#paramkcmslink")
        filename_node = soup.select_one("#paramfilename")
        dbcode_node = soup.select_one("#paramdbcode")
        dbname_node = soup.select_one("#paramdbname")
        resource_node = soup.select_one("#paramresource")
        if api_host_node and api_host_node.get("value"):
            context["api_host"] = api_host_node.get("value", context["api_host"])
        if kcms_node and kcms_node.get("value"):
            context["kcms_link"] = unescape(kcms_node.get("value", ""))
        if filename_node and filename_node.get("value"):
            context["filename"] = filename_node.get("value", context["filename"])
        if dbcode_node and dbcode_node.get("value"):
            context["dbcode"] = dbcode_node.get("value", context["dbcode"])
        if dbname_node and dbname_node.get("value"):
            context["dbname"] = dbname_node.get("value", context["dbname"])
        if resource_node and resource_node.get("value"):
            context["resource"] = resource_node.get("value", context["resource"])
        abstract = extract_detail_abstract(soup)
        if abstract:
            context["abstract"] = abstract
        parsed_metadata = extract_detail_metadata(soup)
        context.update({field_name: value for field_name, value in parsed_metadata.items() if value})
    return context


def clean_abstract_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", unescape(text or "")).strip()
    return re.sub(r"^(摘要|摘\s*要)[:：\s]*", "", cleaned).strip()


def clean_detail_metadata_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", unescape(text or "")).strip()
    return cleaned.strip(";；,，")


def _node_text(node: Any) -> str:
    if hasattr(node, "get_text"):
        return node.get_text(" ", strip=True)
    return str(node).strip()


def _strip_metadata_label(text: str, labels: tuple[str, ...]) -> str:
    value = clean_detail_metadata_text(text)
    for label in labels:
        value = re.sub(rf"^{re.escape(label)}\s*[:：]?", "", value)
    return value.strip(":：")


def _metadata_label_regex(labels: tuple[str, ...]) -> str:
    parts = []
    for label in labels:
        if len(label) <= 2:
            parts.append(rf"{re.escape(label)}\s*[:：]")
        else:
            parts.append(rf"{re.escape(label)}\s*[:：]?")
    return "|".join(parts)


def _metadata_value_after_label(node: Any, labels: tuple[str, ...]) -> str:
    parent = node.parent
    if not parent:
        return ""
    parts: list[str] = []
    collecting = False
    label_pattern = re.compile(rf"^({_metadata_label_regex(labels)})")
    stop_pattern = re.compile(rf"^({'|'.join(re.escape(label) for label in ALL_DETAIL_METADATA_LABELS)})\s*[:：]")
    for child in parent.children:
        text = _node_text(child)
        if not text:
            continue
        if child is node or (hasattr(node, "parent") and child is node.parent):
            collecting = True
            remainder = _strip_metadata_label(text, labels)
            if remainder:
                parts.append(remainder)
            continue
        if not collecting:
            if label_pattern.match(clean_detail_metadata_text(text)):
                collecting = True
                remainder = _strip_metadata_label(text, labels)
                if remainder:
                    parts.append(remainder)
            continue
        cleaned = clean_detail_metadata_text(text)
        if stop_pattern.match(cleaned):
            break
        parts.append(cleaned)
    return clean_detail_metadata_text("；".join(part for part in parts if part))


def _metadata_value_from_container(node: Any, labels: tuple[str, ...]) -> str:
    parent = node.parent
    containers = []
    if parent:
        containers.append(parent)
        if parent.parent:
            containers.append(parent.parent)
    for container in containers:
        raw_text = clean_detail_metadata_text(container.get_text(" ", strip=True) if hasattr(container, "get_text") else str(container))
        for label in labels:
            match = re.match(rf"^{re.escape(label)}[:：](.+)$", raw_text)
            if match:
                value = clean_detail_metadata_text(match.group(1))
                if value:
                    return value
    return ""


def _clean_institution_text(text: str) -> str:
    return re.sub(r"^\d+[.．、]\s*", "", clean_detail_metadata_text(text))


def _extract_author_affiliation_links(soup: BeautifulSoup) -> str:
    selectors = (
        ".affiliations",
        ".affiliation",
        ".author-affiliation",
        ".author-affiliations",
        ".orgn",
        ".organization",
        "[class*=affiliation]",
        "[class*=orgn]",
    )
    for selector in selectors:
        for container in soup.select(selector):
            link_texts = [_clean_institution_text(link.get_text(" ", strip=True)) for link in container.select("a")]
            value = clean_detail_metadata_text("；".join(text for text in link_texts if text))
            if value:
                return value
    organ_link_texts = [
        _clean_institution_text(link.get_text(" ", strip=True))
        for link in soup.select('.wx-tit h3.author a[href*="/organ/detail"], .brief h3.author a[href*="/organ/detail"]')
    ]
    return clean_detail_metadata_text("；".join(text for text in organ_link_texts if text))


def extract_detail_metadata(soup: BeautifulSoup) -> dict[str, str]:
    metadata = {field_name: "" for field_name in DETAIL_METADATA_LABELS}
    all_label_pattern = re.compile(rf"^({'|'.join(_metadata_label_regex(labels) for labels in DETAIL_METADATA_LABELS.values())})")
    for node in soup.find_all(string=all_label_pattern):
        for field_name, labels in DETAIL_METADATA_LABELS.items():
            if metadata[field_name]:
                continue
            raw_node_text = clean_detail_metadata_text(str(node))
            if not re.match(rf"^({_metadata_label_regex(labels)})", raw_node_text):
                continue
            value = _metadata_value_after_label(node, labels) or _metadata_value_from_container(node, labels)
            if value:
                metadata[field_name] = value
    if not metadata["institution"]:
        metadata["institution"] = _extract_author_affiliation_links(soup)
    return metadata


def extract_detail_abstract(soup: BeautifulSoup) -> str:
    selectors = (
        "#ChDivSummary",
        "span#ChDivSummary",
        ".abstract-text",
        ".abstract",
        ".xx_font",
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            abstract = clean_abstract_text(node.get_text(" ", strip=True))
            if abstract:
                return abstract

    for node in soup.find_all(string=re.compile(r"^\s*摘要[:：]")):
        abstract = clean_abstract_text(str(node))
        if abstract:
            return abstract
        parent = node.parent
        if parent:
            sibling_texts: list[str] = []
            for sibling in parent.find_next_siblings():
                text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                if text:
                    sibling_texts.append(text)
                if sibling_texts:
                    break
            abstract = clean_abstract_text(" ".join(sibling_texts))
            if abstract:
                return abstract
    return ""



def extract_citation_metrics_context(article: dict[str, Any], html: str | None = None) -> dict[str, str]:
    return build_detail_context(article, html)



def build_citation_list_request(
    article: dict[str, Any],
    relation_type: str,
    file_type: str,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    detail_context = article.get("detail_context", {})
    api_host = detail_context.get("api_host", "")
    kcms_link = detail_context.get("kcms_link", "")
    api_path = RELATION_API_PATH_MAP[relation_type]
    start = (page - 1) * page_size + 1
    return {
        "url": f"{api_host}/citation-api/v1/literature/{api_path}?{kcms_link}&type={file_type}&start={start}&size={page_size}",
        "headers": {"Accept": "application/json, text/plain, */*"},
    }



def build_citation_metrics_request(article: dict[str, Any]) -> dict[str, Any]:
    detail_context = article.get("detail_context", {})
    api_host = detail_context.get("api_host", "")
    kcms_link = detail_context.get("kcms_link", "")
    return {
        "url": f"{api_host}/citation-api/v1/literature/quotations/metrics?{kcms_link}",
        "headers": {"Accept": "application/json, text/plain, */*"},
    }



def extract_metadata_value(payload: dict[str, Any], name: str) -> str:
    for item in payload.get("metadata", []):
        if item.get("name") == name:
            return str(item.get("value") or "")
    if name == "vid":
        values = parse_qs(payload.get("kcms_link", "")).get("v", [])
        if values:
            return values[0]
    return ""



def build_reader_references_request(vid: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    start = (page - 1) * page_size + 1
    encoded_vid = quote(vid, safe="")
    return {
        "url": "https://kns.cnki.net/restapi/citation-api/v1/literature/original-references-auth"
        f"?size={page_size}&start={start}&v={encoded_vid}",
        "headers": {"Accept": "application/json, text/javascript, */*; q=0.01"},
    }



def build_reader_detail_request(article: dict[str, Any]) -> dict[str, Any]:
    detail_context = article.get("detail_context", {})
    dataset = detail_context.get("dbname") or article.get("db_name", "")
    payload = {
        "id": article.get("file_name", ""),
        "resource": article.get("db_name", ""),
        "type": article.get("db_type", article.get("resource", "")),
        "dataset": dataset,
        "start": 1,
        "size": 10,
    }
    return {
        "url": "https://kns.cnki.net/restapi/literature-api/v1/articles/detail"
        f"?vv={READER_DETAIL_VV}&clientId={READER_CLIENT_ID}",
        "headers": {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json;charset=UTF-8",
        },
        "json": payload,
    }



def parse_citation_metrics(payload: dict[str, Any]) -> dict[str, int]:
    raw_data = payload.get("data", {}) if payload.get("code") == 0 else {}
    if isinstance(raw_data, list):
        data = {item.get("name", "").replace("-", ""): int(item.get("value", 0) or 0) for item in raw_data}
    else:
        data = {key.replace("-", ""): int(value or 0) for key, value in raw_data.items()}
    return {field_name: int(data.get(api_name, 0) or 0) for api_name, field_name in RELATION_TYPE_FIELD_MAP.items()}



AUTHOR_FALLBACK_FIELDS = (
    "Author",
    "Authors",
    "authorName",
    "authorText",
    "showAuthor",
    "creator",
    "creatorName",
)



def clean_author_names(value: Any) -> str:
    if isinstance(value, list):
        author_names: list[str] = []
        for author in value:
            if not author:
                continue
            raw_name = author.get("name") if isinstance(author, dict) else author
            normalized_name = clean_author_names(raw_name)
            if normalized_name:
                author_names.append(normalized_name)
        return ";".join(author_names)

    text = str(value or "")
    text = re.sub(r"[；;,，]+", ";", text)
    names = [name.strip() for name in text.split(";")]
    return ";".join(name for name in names if name)


def extract_relation_authors(item: dict[str, Any], metadata: dict[str, Any]) -> str:
    for value in (item.get("author"), item.get("authors"), metadata.get("AU", "")):
        authors = clean_author_names(value)
        if authors:
            return authors
    for field_name in AUTHOR_FALLBACK_FIELDS:
        authors = clean_author_names(item.get(field_name))
        if authors:
            return authors
    return ""



def normalize_relation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        metadata = {entry.get("name"): entry.get("value", "") for entry in item.get("metadata", [])}
        authors = extract_relation_authors(item, metadata)
        normalized_items.append(
            {
                "title": item.get("title") or metadata.get("TI", ""),
                "authors": authors,
                "journal": item.get("publishName") or item.get("journal") or metadata.get("LY", ""),
                "year": item.get("year") or metadata.get("YE", ""),
                "file_name": item.get("fileName") or item.get("file_name") or metadata.get("FN", ""),
                "db_name": item.get("dBName") or item.get("db_name") or metadata.get("TN", ""),
                "db_type": item.get("type") or item.get("db_type") or metadata.get("resource", ""),
            }
        )
    return normalized_items
