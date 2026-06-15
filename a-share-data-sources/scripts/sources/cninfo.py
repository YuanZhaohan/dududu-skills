from __future__ import annotations

from datetime import datetime
from typing import Any
import time

import requests

from common import make_record_key, normalize_symbol, now_iso


SOURCE = "cninfo_direct"
URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STOCK_JSON_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"


_ORG_CACHE: dict[str, str] | None = None


def _normalize_published_at(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")
    text = str(value).strip()
    if text.isdigit():
        seconds = int(text) / 1000 if len(text) >= 13 else int(text)
        return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")
    return text


def _fetch_org_id(symbol: str) -> str:
    global _ORG_CACHE
    if _ORG_CACHE is None:
        response = requests.get(STOCK_JSON_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
        _ORG_CACHE = {
            str(item.get("code", "")): str(item.get("orgId", ""))
            for item in payload.get("stockList", [])
            if item.get("code") and item.get("orgId")
        }
    return _ORG_CACHE.get(symbol, "")


def fetch_announcements(symbol: str, *, page_size: int = 30, page_num: int = 1) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plain = normalize_symbol(symbol)["plain"]
    column = "sse" if plain.startswith("6") else "szse"
    org_id = _fetch_org_id(plain)
    stock_item = f"{plain},{org_id}" if org_id else plain
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    }
    data = {
        "pageNum": page_num,
        "pageSize": page_size,
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_item,
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = None
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.post(URL, headers=headers, data=data, timeout=15)
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * attempt)
    if response is None:
        raise RuntimeError(f"CNINFO request failed: {last_error}")
    payload = response.json()
    records: list[dict[str, Any]] = []
    for item in payload.get("announcements", []) or []:
        title = item.get("announcementTitle", "")
        announcement_id = item.get("announcementId") or item.get("adjunctUrl") or title
        published_at = _normalize_published_at(item.get("announcementTime"))
        adjunct_url = item.get("adjunctUrl", "")
        link = f"http://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else ""
        records.append(
            {
                "symbol": plain,
                "section": "announcements",
                "source": SOURCE,
                "source_url": link,
                "published_at": published_at or "",
                "fetched_at": now_iso(),
                "record_key": make_record_key(plain, announcement_id, title),
                "payload": {
                    "announcement_id": announcement_id,
                    "title": title,
                    "category": item.get("announcementTypeName", ""),
                    "link": link,
                    "raw": item,
                },
            }
        )
    return records, payload
