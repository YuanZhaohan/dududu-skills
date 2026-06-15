from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin

from common import http_get, make_record_key, normalize_symbol, now_iso


SOURCE = "jiuyangongshe"
BASE_URL = "https://www.jiuyangongshe.com"


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    return " ".join(text.split())


def fetch_mentions(
    symbol: str,
    *,
    keyword: str | None = None,
    max_items: int = 30,
    timeout: int = 8,
    retries: int = 2,
) -> list[dict[str, Any]]:
    plain = normalize_symbol(symbol)["plain"]
    query = keyword or plain
    url = f"{BASE_URL}/search/new?k={quote(query)}"
    response = http_get(
        url,
        timeout=timeout,
        retries=retries,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": BASE_URL,
        },
    )
    response.encoding = "utf-8"
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", response.text, re.S | re.I):
        href = match.group(1).strip()
        if not href.startswith("/a/"):
            continue
        snippet = _clean_text(match.group(2))
        if len(snippet) < 12:
            continue
        link = urljoin(BASE_URL, href)
        if link in seen:
            continue
        seen.add(link)
        title = snippet[:80]
        records.append(
            {
                "symbol": plain,
                "section": "forum",
                "source": SOURCE,
                "source_url": link,
                "published_at": "",
                "fetched_at": now_iso(),
                "record_key": make_record_key(plain, SOURCE, link, title),
                "payload": {
                    "title": title,
                    "snippet": snippet[:500],
                    "query": query,
                    "platform": "韭研公社",
                },
            }
        )
        if len(records) >= max_items:
            break
    return records
