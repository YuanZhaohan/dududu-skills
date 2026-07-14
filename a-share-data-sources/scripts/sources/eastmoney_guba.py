from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

from common import DEFAULT_SYMBOLS_PATH, STATE_DIR, clean_json_value, ensure_data_dirs, http_get, make_record_key, normalize_symbol, now_iso


SOURCE = "eastmoney_guba"
GUBA_DB_PATH = STATE_DIR / "eastmoney_guba.sqlite"
EASTMONEY_SYMBOLS_METADATA_PATH = STATE_DIR / "eastmoney_symbols.json"
EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_HS_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_BJ_A_FS = "m:0+t:81+s:2048"
A_SHARE_CODE_RE = re.compile(r"^(?:60\d{4}|688\d{3}|689\d{3}|000\d{3}|001\d{3}|002\d{3}|003\d{3}|300\d{3}|301\d{3}|4\d{5}|8\d{5}|920\d{3})$")
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://guba.eastmoney.com/",
}


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



def _is_a_share_code(code: str) -> bool:
    return bool(A_SHARE_CODE_RE.fullmatch(code))


def _market_from_eastmoney(row: dict[str, Any]) -> str:
    code = str(row.get("f12") or "").strip()
    market_id = str(row.get("f13") or "").strip()
    if code.startswith(("4", "8", "920")) or (market_id == "0" and code.startswith(("4", "8"))):
        return "bj"
    if code.startswith("6"):
        return "sh"
    return "sz"


def _clist_url(fs: str, page: int, page_size: int) -> str:
    fields = "f12,f13,f14"
    return (
        f"{EASTMONEY_CLIST_URL}?pn={page}&pz={page_size}&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs={fs}&fields={fields}"
    )




def _fetch_clist_data(fs: str, page: int, page_size: int, *, timeout: int, retries: int) -> dict[str, Any]:
    outer_attempts = max(4, retries + 2)
    delay = 1.5
    last_error = ""
    for attempt in range(1, outer_attempts + 1):
        try:
            response = http_get(
                _clist_url(fs, page, page_size),
                timeout=timeout,
                retries=max(1, retries),
                headers=DEFAULT_HEADERS,
            )
            payload = response.json()
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            if isinstance(rows, list):
                return data
            last_error = "missing diff list"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        if attempt < outer_attempts:
            time.sleep(delay)
            delay = min(delay * 1.8, 8.0)
    raise RuntimeError(f"Eastmoney clist page failed after {outer_attempts} attempts: page={page}, error={last_error}")

def _fetch_a_share_symbols_akshare(*, include_bj: bool = True) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError(f"AkShare is not installed: {exc}") from exc

    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_info_a_code_name()
    if df is None or df.empty:
        raise RuntimeError("AkShare stock_info_a_code_name returned empty data")
    columns = {str(col).strip().lower(): col for col in df.columns}
    code_col = columns.get("code") or columns.get("代码") or columns.get("证券代码")
    name_col = columns.get("name") or columns.get("名称") or columns.get("证券简称")
    if code_col is None or name_col is None:
        raise RuntimeError(f"AkShare stock list columns not recognized: {list(df.columns)}")

    seen: set[str] = set()
    symbols: list[dict[str, Any]] = []
    for row in df[[code_col, name_col]].itertuples(index=False, name=None):
        code = str(row[0] or "").strip().zfill(6)
        if not _is_a_share_code(code) or code in seen:
            continue
        market = _market_from_eastmoney({"f12": code})
        if not include_bj and market == "bj":
            continue
        seen.add(code)
        symbols.append(
            {
                "symbol": code,
                "name": str(row[1] or "").strip(),
                "market": market,
                "pool": "akshare_a",
            }
        )
    if not symbols:
        raise RuntimeError("AkShare stock list returned no A-share symbols after filtering")
    return sorted(symbols, key=lambda item: item["symbol"])


def fetch_a_share_symbols(
    *,
    include_bj: bool = True,
    timeout: int = 8,
    retries: int = 2,
    page_size: int = 100,
    max_pages: int | None = None,
    allow_akshare_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Fetch the current A-share universe.

    Eastmoney is the primary source. AkShare is an automatic fallback because
    Eastmoney quote-list pages can occasionally close connections mid-run.
    """
    try:
        page_size = max(1, min(int(page_size), 100))
        pools = [("hs_a", EASTMONEY_HS_A_FS)]
        if include_bj:
            pools.append(("bj_a", EASTMONEY_BJ_A_FS))

        seen: set[str] = set()
        symbols: list[dict[str, Any]] = []
        for pool_name, fs in pools:
            page = 1
            total = 0
            while True:
                data = _fetch_clist_data(fs, page, page_size, timeout=timeout, retries=retries)
                total = int(data.get("total") or total or 0)
                rows = data.get("diff") or []
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("f12") or "").strip()
                    if not _is_a_share_code(code) or code in seen:
                        continue
                    seen.add(code)
                    symbols.append(
                        {
                            "symbol": code,
                            "name": _fix_double_encoded(str(row.get("f14") or "").strip()),
                            "market": _market_from_eastmoney(row),
                            "pool": pool_name,
                        }
                    )
                if max_pages is not None and page >= max_pages:
                    break
                if total and page * page_size >= total:
                    break
                page += 1
        return sorted(symbols, key=lambda item: item["symbol"])
    except Exception:
        if allow_akshare_fallback:
            return _fetch_a_share_symbols_akshare(include_bj=include_bj)
        raise

def refresh_symbols_file(
    *,
    symbols_path=DEFAULT_SYMBOLS_PATH,
    metadata_path=EASTMONEY_SYMBOLS_METADATA_PATH,
    include_bj: bool = True,
    timeout: int = 8,
    retries: int = 2,
    max_pages: int | None = None,
) -> dict[str, Any]:
    symbols = fetch_a_share_symbols(include_bj=include_bj, timeout=timeout, retries=retries, max_pages=max_pages)
    if not symbols:
        raise RuntimeError("Eastmoney symbol universe returned no A-share symbols")
    ensure_data_dirs()
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()
    source_name = "akshare_stock_info_a_code_name" if all(item.get("pool") == "akshare_a" for item in symbols) else "eastmoney_clist"
    lines = [
        "# Generated from Eastmoney quote list API by a-share-data-sources.",
        f"# generated_at={generated_at}",
        "# One A-share symbol per line. Names are stored in data/state/eastmoney_symbols.json.",
        *[item["symbol"] for item in symbols],
    ]
    symbols_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    metadata = {
        "generated_at": generated_at,
        "source": source_name,
        "include_bj": include_bj,
        "symbol_count": len(symbols),
        "symbols": symbols,
    }
    metadata_path.write_text(json.dumps(clean_json_value(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "source": source_name,
        "symbol_count": len(symbols),
        "symbols_path": str(symbols_path),
        "metadata_path": str(metadata_path),
        "include_bj": include_bj,
        "generated_at": generated_at,
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _to_bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _page_url(symbol: str, page: int) -> str:
    plain = normalize_symbol(symbol)["plain"]
    return f"https://guba.eastmoney.com/list,{plain}_{page}.html"


def _extract_article_list(html: str) -> dict[str, Any]:
    match = re.search(r"var article_list\s*=\s*({.*?});", html, re.DOTALL)
    if not match:
        return {}
    return json.loads(match.group(1))


def _within_date_window(published_at: str, start_date: str | None, end_date: str | None) -> bool:
    published = _parse_date(published_at)
    if not published:
        return False
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start and published < start:
        return False
    if end and published > end:
        return False
    return True


def _should_stop_at_page(rows: list[dict[str, Any]], start_date: str | None) -> bool:
    start = _parse_date(start_date)
    if not start or not rows:
        return False
    dates = [_parse_date(str(row.get("post_publish_time", ""))) for row in rows]
    dates = [item for item in dates if item]
    return bool(dates and min(dates) < start)


def _record_from_item(plain: str, item: dict[str, Any]) -> dict[str, Any]:
    post_id = item.get("post_id")
    published_at = str(item.get("post_publish_time", ""))
    title = _fix_double_encoded(item.get("post_title", ""))
    link = f"https://guba.eastmoney.com/news,{plain},{post_id}.html"
    return {
        "symbol": plain,
        "section": "forum",
        "source": SOURCE,
        "source_url": link,
        "published_at": published_at,
        "fetched_at": now_iso(),
        "record_key": make_record_key(plain, SOURCE, post_id or published_at, title),
        "payload": {
            "title": title,
            "author": item.get("user_nickname"),
            "author_id": item.get("user_id"),
            "clicks": item.get("post_click_count") or 0,
            "replies": item.get("post_comment_count") or 0,
            "forwards": item.get("post_forward_count") or 0,
            "post_id": post_id,
            "post_publish_time": published_at,
            "post_last_time": item.get("post_last_time"),
            "post_display_time": item.get("post_display_time"),
            "post_type": item.get("post_type"),
            "post_state": item.get("post_state"),
            "post_from_num": item.get("post_from_num"),
            "post_top_status": item.get("post_top_status"),
            "post_has_pic": item.get("post_has_pic"),
            "post_has_video": item.get("post_has_video"),
            "media_type": item.get("media_type"),
            "cms_media_type": item.get("cms_media_type"),
            "bullish_bearish": item.get("bullish_bearish"),
            "stockbar_code": item.get("stockbar_code"),
            "stockbar_name": item.get("stockbar_name"),
            "stockbar_type": item.get("stockbar_type"),
            "stockbar_exchange": item.get("stockbar_exchange"),
            "v_user_code": item.get("v_user_code"),
            "user_is_majia": item.get("user_is_majia"),
            "user_extendinfos": item.get("user_extendinfos"),
            "post_source_id": item.get("post_source_id"),
            "zmt_article": item.get("zmt_article"),
            "modules": item.get("modules"),
            "spec_column": item.get("spec_column"),
            "notice_type": item.get("notice_type"),
            "notice_type_code": item.get("notice_type_code"),
            "raw_list_item": clean_json_value(item),
        },
    }


def fetch_posts(
    symbol: str,
    *,
    pages: int = 2,
    target_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    stop_at_start_date: bool = True,
    timeout: int = 5,
    retries: int = 2,
) -> list[dict[str, Any]]:
    plain = normalize_symbol(symbol)["plain"]
    if target_date and not start_date and not end_date:
        start_date = target_date
        end_date = target_date
    if not start_date and not end_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
        end_date = start_date
    posts: list[dict[str, Any]] = []
    failed_pages = 0
    for page in range(1, pages + 1):
        url = _page_url(plain, page)
        try:
            response = http_get(url, timeout=timeout, retries=retries, headers=DEFAULT_HEADERS)
            payload = _extract_article_list(response.text)
        except Exception:
            failed_pages += 1
            continue
        if not payload:
            continue
        rows = [row for row in payload.get("re", []) if isinstance(row, dict)]
        for item in rows:
            published_at = str(item.get("post_publish_time", ""))
            if not _within_date_window(published_at, start_date, end_date):
                continue
            posts.append(_record_from_item(plain, item))
        if stop_at_start_date and _should_stop_at_page(rows, start_date):
            break
    if failed_pages >= pages and not posts:
        raise RuntimeError(f"all Eastmoney Guba pages failed for {plain}")
    return posts


def init_guba_db(db_path=GUBA_DB_PATH) -> None:
    ensure_data_dirs()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guba_posts (
                post_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                source_url TEXT,
                title TEXT,
                author_id TEXT,
                author_name TEXT,
                publish_time TEXT,
                publish_date TEXT,
                last_time TEXT,
                display_time TEXT,
                click_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                forward_count INTEGER NOT NULL DEFAULT 0,
                top_status INTEGER NOT NULL DEFAULT 0,
                post_type INTEGER NOT NULL DEFAULT 0,
                post_state INTEGER NOT NULL DEFAULT 0,
                has_pic INTEGER NOT NULL DEFAULT 0,
                has_video INTEGER NOT NULL DEFAULT 0,
                bullish_bearish INTEGER NOT NULL DEFAULT 0,
                media_type INTEGER NOT NULL DEFAULT 0,
                cms_media_type INTEGER NOT NULL DEFAULT 0,
                stockbar_name TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guba_daily_stats (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                post_count INTEGER NOT NULL,
                total_clicks INTEGER NOT NULL,
                total_comments INTEGER NOT NULL,
                total_forwards INTEGER NOT NULL,
                hot_post_count INTEGER NOT NULL,
                top_post_count INTEGER NOT NULL,
                pic_post_count INTEGER NOT NULL,
                video_post_count INTEGER NOT NULL,
                unique_author_count INTEGER NOT NULL,
                bullish_bearish_0_count INTEGER NOT NULL,
                bullish_bearish_1_count INTEGER NOT NULL,
                bullish_bearish_2_count INTEGER NOT NULL,
                max_clicks INTEGER NOT NULL,
                max_comments INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_guba_posts_symbol_date ON guba_posts(symbol, publish_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_guba_posts_publish_time ON guba_posts(publish_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_guba_posts_author ON guba_posts(author_id)")
        conn.commit()


def upsert_guba_posts(records: list[dict[str, Any]], db_path=GUBA_DB_PATH) -> int:
    if not records:
        init_guba_db(db_path)
        return 0
    init_guba_db(db_path)
    rows: list[tuple[Any, ...]] = []
    for record in records:
        payload = record.get("payload", {})
        post_id = payload.get("post_id")
        if not post_id:
            continue
        raw_item = payload.get("raw_list_item") or payload
        rows.append(
            (
                str(post_id),
                record.get("symbol", ""),
                record.get("source_url", ""),
                payload.get("title", ""),
                payload.get("author_id", ""),
                payload.get("author", ""),
                record.get("published_at", ""),
                str(record.get("published_at", ""))[:10],
                payload.get("post_last_time", ""),
                payload.get("post_display_time", ""),
                _to_int(payload.get("clicks")),
                _to_int(payload.get("replies")),
                _to_int(payload.get("forwards")),
                _to_int(payload.get("post_top_status")),
                _to_int(payload.get("post_type")),
                _to_int(payload.get("post_state")),
                _to_bool_int(payload.get("post_has_pic")),
                _to_bool_int(payload.get("post_has_video")),
                _to_int(payload.get("bullish_bearish")),
                _to_int(payload.get("media_type")),
                _to_int(payload.get("cms_media_type")),
                payload.get("stockbar_name", ""),
                json.dumps(clean_json_value(raw_item), ensure_ascii=False),
                record.get("fetched_at", now_iso()),
                now_iso(),
            )
        )
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO guba_posts (
                post_id, symbol, source_url, title, author_id, author_name,
                publish_time, publish_date, last_time, display_time,
                click_count, comment_count, forward_count, top_status,
                post_type, post_state, has_pic, has_video, bullish_bearish,
                media_type, cms_media_type, stockbar_name, raw_json, fetched_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                symbol=excluded.symbol,
                source_url=excluded.source_url,
                title=excluded.title,
                author_id=excluded.author_id,
                author_name=excluded.author_name,
                publish_time=excluded.publish_time,
                publish_date=excluded.publish_date,
                last_time=excluded.last_time,
                display_time=excluded.display_time,
                click_count=excluded.click_count,
                comment_count=excluded.comment_count,
                forward_count=excluded.forward_count,
                top_status=excluded.top_status,
                post_type=excluded.post_type,
                post_state=excluded.post_state,
                has_pic=excluded.has_pic,
                has_video=excluded.has_video,
                bullish_bearish=excluded.bullish_bearish,
                media_type=excluded.media_type,
                cms_media_type=excluded.cms_media_type,
                stockbar_name=excluded.stockbar_name,
                raw_json=excluded.raw_json,
                fetched_at=excluded.fetched_at,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def refresh_guba_daily_stats(
    symbol: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    hot_click_threshold: int = 1000,
    hot_comment_threshold: int = 20,
    db_path=GUBA_DB_PATH,
) -> int:
    init_guba_db(db_path)
    clauses = ["publish_date IS NOT NULL", "publish_date != ''"]
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(normalize_symbol(symbol)["plain"])
    if start_date:
        clauses.append("publish_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("publish_date <= ?")
        params.append(end_date)
    where = " AND ".join(clauses)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                symbol,
                publish_date,
                COUNT(*) AS post_count,
                COALESCE(SUM(click_count), 0) AS total_clicks,
                COALESCE(SUM(comment_count), 0) AS total_comments,
                COALESCE(SUM(forward_count), 0) AS total_forwards,
                COALESCE(SUM(CASE WHEN click_count >= ? OR comment_count >= ? THEN 1 ELSE 0 END), 0) AS hot_post_count,
                COALESCE(SUM(CASE WHEN top_status != 0 THEN 1 ELSE 0 END), 0) AS top_post_count,
                COALESCE(SUM(has_pic), 0) AS pic_post_count,
                COALESCE(SUM(has_video), 0) AS video_post_count,
                COUNT(DISTINCT NULLIF(author_id, '')) AS unique_author_count,
                COALESCE(SUM(CASE WHEN bullish_bearish = 0 THEN 1 ELSE 0 END), 0) AS bullish_bearish_0_count,
                COALESCE(SUM(CASE WHEN bullish_bearish = 1 THEN 1 ELSE 0 END), 0) AS bullish_bearish_1_count,
                COALESCE(SUM(CASE WHEN bullish_bearish = 2 THEN 1 ELSE 0 END), 0) AS bullish_bearish_2_count,
                COALESCE(MAX(click_count), 0) AS max_clicks,
                COALESCE(MAX(comment_count), 0) AS max_comments
            FROM guba_posts
            WHERE {where}
            GROUP BY symbol, publish_date
            """,
            [hot_click_threshold, hot_comment_threshold, *params],
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO guba_daily_stats (
                symbol, trade_date, post_count, total_clicks, total_comments, total_forwards,
                hot_post_count, top_post_count, pic_post_count, video_post_count,
                unique_author_count, bullish_bearish_0_count, bullish_bearish_1_count,
                bullish_bearish_2_count, max_clicks, max_comments, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                post_count=excluded.post_count,
                total_clicks=excluded.total_clicks,
                total_comments=excluded.total_comments,
                total_forwards=excluded.total_forwards,
                hot_post_count=excluded.hot_post_count,
                top_post_count=excluded.top_post_count,
                pic_post_count=excluded.pic_post_count,
                video_post_count=excluded.video_post_count,
                unique_author_count=excluded.unique_author_count,
                bullish_bearish_0_count=excluded.bullish_bearish_0_count,
                bullish_bearish_1_count=excluded.bullish_bearish_1_count,
                bullish_bearish_2_count=excluded.bullish_bearish_2_count,
                max_clicks=excluded.max_clicks,
                max_comments=excluded.max_comments,
                updated_at=excluded.updated_at
            """,
            [(*row, now_iso()) for row in rows],
        )
        conn.commit()
    return len(rows)


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
