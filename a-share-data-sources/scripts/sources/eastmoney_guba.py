from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from common import http_get, make_record_key, normalize_symbol, now_iso


SOURCE = "eastmoney_guba"


def _fix_double_encoded(text: str | None) -> str:
    if not text:
        return ""
    try:
        decoded = text.encode("latin-1", errors="replace").decode("utf-8", errors="replace")
        if any("\u4e00" <= char <= "\u9fff" for char in decoded):
            return decoded
    except Exception:
        pass
    return text


def fetch_posts(
    symbol: str,
    *,
    pages: int = 2,
    target_date: str | None = None,
    timeout: int = 5,
    retries: int = 2,
) -> list[dict[str, Any]]:
    plain = normalize_symbol(symbol)["plain"]
    target = target_date or datetime.now().strftime("%Y-%m-%d")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://guba.eastmoney.com/",
    }
    posts: list[dict[str, Any]] = []
    failed_pages = 0
    for page in range(1, pages + 1):
        url = f"https://guba.eastmoney.com/list,{plain}_{page}.html"
        try:
            response = http_get(url, timeout=timeout, retries=retries, headers=headers)
            match = re.search(r"var article_list\s*=\s*({.*?});", response.text, re.DOTALL)
        except Exception:
            failed_pages += 1
            continue
        if not match:
            continue
        payload = json.loads(match.group(1))
        for item in payload.get("re", []):
            published_at = str(item.get("post_publish_time", ""))
            if published_at[:10] != target:
                continue
            title = _fix_double_encoded(item.get("post_title", ""))
            link = f"https://guba.eastmoney.com/news,{plain},{item.get('post_id')}.html"
            record = {
                "symbol": plain,
                "section": "forum",
                "source": SOURCE,
                "source_url": link,
                "published_at": published_at,
                "fetched_at": now_iso(),
                "record_key": make_record_key(plain, SOURCE, published_at, title),
                "payload": {
                    "title": title,
                    "author": item.get("user_nickname"),
                    "clicks": item.get("post_click_count") or 0,
                    "replies": item.get("post_comment_count") or 0,
                    "post_id": item.get("post_id"),
                },
            }
            posts.append(record)
    if failed_pages >= pages and not posts:
        raise RuntimeError(f"all Eastmoney Guba pages failed for {plain}")
    return posts


def summarize_posts(posts: list[dict[str, Any]], *, target_date: str | None = None) -> dict[str, Any]:
    target = target_date or datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(target, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    today_posts = [p for p in posts if str(p.get("published_at", ""))[:10] == target]
    yesterday_posts = [p for p in posts if str(p.get("published_at", ""))[:10] == yesterday]
    return {
        "target_date": target,
        "today_count": len(today_posts),
        "yesterday_count": len(yesterday_posts),
        "today_clicks": sum(int(p.get("payload", {}).get("clicks") or 0) for p in today_posts),
        "yesterday_clicks": sum(int(p.get("payload", {}).get("clicks") or 0) for p in yesterday_posts),
    }
