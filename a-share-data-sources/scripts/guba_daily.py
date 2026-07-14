from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    DEFAULT_SYMBOLS_PATH,
    STATE_DIR,
    clean_json_value,
    emit_json,
    load_symbols,
    normalize_symbol,
    now_iso,
    stable_hash,
)
from news_data import summarize_result, update_forum  # noqa: E402
from sources.eastmoney_guba import GUBA_DB_PATH, fetch_a_share_symbols, refresh_symbols_file  # noqa: E402


DEFAULT_BATCH_SIZE = 500
DEFAULT_PAGES = 3
DAILY_LAST_RUN_PATH = STATE_DIR / "eastmoney_guba_daily_last_run.json"
REPORT_LAST_RUN_PATH = STATE_DIR / "eastmoney_guba_report_last_run.json"
SENTIMENT_OUTPUT_DIR = STATE_DIR / "guba_sentiment"


@dataclass
class GubaDailyConfig:
    symbols: list[str] | None = None
    run_all: bool = False
    plan_only: bool = False
    batch_index: int | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    pages: int = DEFAULT_PAGES
    date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    refresh_symbols: str = "auto"
    include_bj: bool = True
    timeout: int = 8
    retries: int = 2
    reset: bool = False
    report_only: bool = False
    no_report: bool = False
    report_on_partial: bool = False
    no_write_report: bool = False
    top_n: int = 20


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _validate_date(value: str | None, name: str) -> None:
    if not value:
        return
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}") from exc


def _date_window(config: GubaDailyConfig) -> tuple[str, str]:
    _validate_date(config.date, "date")
    _validate_date(config.start_date, "start_date")
    _validate_date(config.end_date, "end_date")
    if config.date and (config.start_date or config.end_date):
        raise ValueError("Use either date or start_date/end_date, not both")
    if config.start_date or config.end_date:
        start = config.start_date or config.end_date
        end = config.end_date or config.start_date
    else:
        start = end = config.date or _today()
    if start and end and start > end:
        raise ValueError("start_date must be <= end_date")
    return str(start), str(end)


def _normalize_symbols(symbols: list[str] | None) -> list[str]:
    source = symbols if symbols else load_symbols()
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in source:
        plain = normalize_symbol(symbol)["plain"]
        if plain in seen:
            continue
        seen.add(plain)
        normalized.append(plain)
    return normalized


def _should_refresh_symbols(config: GubaDailyConfig) -> bool:
    if config.symbols:
        return False
    if config.plan_only:
        return False
    if config.refresh_symbols == "always":
        return True
    if config.refresh_symbols == "never":
        return False
    if not DEFAULT_SYMBOLS_PATH.exists():
        return True
    try:
        if len(load_symbols()) < 1000:
            return True
    except Exception:
        return True
    return _effective_run_all(config) or config.batch_index in (None, 0)


def _daily_checkpoint_path(run_id: str) -> Path:
    return STATE_DIR / f"eastmoney_guba_daily_{run_id}.json"


def _run_signature(symbols: list[str], config: GubaDailyConfig, start_date: str, end_date: str) -> tuple[str, dict[str, Any]]:
    payload = {
        "source": "eastmoney_guba_daily",
        "symbols_hash": stable_hash("\n".join(symbols), 24),
        "symbol_count": len(symbols),
        "batch_size": config.batch_size,
        "pages": config.pages,
        "start_date": start_date,
        "end_date": end_date,
        "include_bj": config.include_bj,
    }
    return stable_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True), 16), payload


def _load_daily_checkpoint(path: Path, signature: dict[str, Any], reset: bool) -> dict[str, Any]:
    if reset:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if path.exists():
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            if checkpoint.get("signature") == signature:
                checkpoint.setdefault("completed_batches", {})
                checkpoint.setdefault("failed_batches", {})
                return checkpoint
        except Exception:
            pass
    return {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "signature": signature,
        "completed_batches": {},
        "failed_batches": {},
    }


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_json_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _batch_symbol_count(total_symbols: int, batch_size: int, batch_index: int) -> int:
    start = batch_index * batch_size
    if start >= total_symbols:
        return 0
    return min(batch_size, total_symbols - start)


def _summarize_batch(batch_index: int, batch_size: int, total_symbols: int, results) -> dict[str, Any]:
    summaries = [summarize_result(result) for result in results]
    ok = bool(summaries) and all(item.get("ok") for item in summaries)
    return {
        "batch_index": batch_index,
        "batch_size": batch_size,
        "selected_symbol_count": _batch_symbol_count(total_symbols, batch_size, batch_index),
        "ok": ok,
        "result_count": len(summaries),
        "record_count": sum(int(item.get("record_count") or 0) for item in summaries),
        "failed_symbols": [item.get("symbol") for item in summaries if not item.get("ok")],
        "results": summaries,
    }


POSITIVE_KEYWORDS = [
    "利好",
    "看多",
    "看涨",
    "看好",
    "买入",
    "加仓",
    "增持",
    "上涨",
    "大涨",
    "涨停",
    "反弹",
    "突破",
    "新高",
    "强势",
    "机会",
    "抄底",
    "拉升",
    "红盘",
    "牛市",
    "回购",
    "分红",
    "超预期",
]

NEGATIVE_KEYWORDS = [
    "利空",
    "看空",
    "看跌",
    "卖出",
    "减仓",
    "下跌",
    "大跌",
    "跌停",
    "破位",
    "亏损",
    "亏钱",
    "被套",
    "套牢",
    "割肉",
    "风险",
    "暴雷",
    "退市",
    "砸盘",
    "崩盘",
    "垃圾",
    "绿盘",
    "不及预期",
]


@dataclass
class SentimentConfig:
    date: str | None = None
    symbols: list[str] | None = None
    db_path: Path = GUBA_DB_PATH
    last_run_path: Path = DAILY_LAST_RUN_PATH
    output_dir: Path = SENTIMENT_OUTPUT_DIR
    top_n: int = 20
    write: bool = True


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _validate_report_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")


def _previous_date(value: str) -> str:
    return (datetime.strptime(value, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def classify_title(title: str) -> dict[str, Any]:
    text = str(title or "")
    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword in text]
    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword in text]
    score = len(positive_hits) - len(negative_hits)
    if score > 0:
        label = "positive"
    elif score < 0:
        label = "negative"
    else:
        label = "neutral"
    return {
        "label": label,
        "score": score,
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
    }


def _read_last_run(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"failed to read last run: {type(exc).__name__}: {str(exc)[:200]}"}
    if payload.get("mode") == "report_only":
        return {
            "ok": False,
            "mode": "report_only",
            "warning": "ignored report-only state; this file should describe the latest crawl run",
            "source_path": str(path),
        }
    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")
    duration = payload.get("duration_seconds")
    if duration is None and started_at and finished_at:
        try:
            start = datetime.fromisoformat(str(started_at))
            finish = datetime.fromisoformat(str(finished_at))
            duration = round((finish - start).total_seconds(), 3)
        except Exception:
            duration = None
    batch_count = int(payload.get("batch_count") or 0)
    completed = payload.get("daily_checkpoint_completed_batches")
    if completed is None:
        completed = sum(1 for batch in payload.get("batches", []) if batch.get("ok") or batch.get("skipped_by_daily_checkpoint"))
    return {
        "ok": payload.get("ok"),
        "mode": payload.get("mode"),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "duration_minutes": round(duration / 60, 2) if isinstance(duration, (int, float)) else None,
        "symbol_count": payload.get("symbol_count"),
        "batch_size": payload.get("batch_size"),
        "batch_count": batch_count,
        "completed_batches": completed,
        "failed_batch_indexes": payload.get("failed_batch_indexes", []),
        "record_count": payload.get("record_count"),
        "pages": payload.get("pages"),
        "run_id": payload.get("run_id"),
        "source_path": str(path),
    }


def _load_rows(conn: sqlite3.Connection, target_date: str, symbols: list[str] | None) -> list[sqlite3.Row]:
    params: list[Any] = [target_date]
    where = "publish_date = ?"
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        where += f" AND symbol IN ({placeholders})"
        params.extend(symbols)
    return conn.execute(
        f"""
        SELECT
            symbol,
            post_id,
            source_url,
            title,
            author_id,
            author_name,
            publish_time,
            publish_date,
            click_count,
            comment_count,
            forward_count,
            top_status,
            has_pic,
            has_video,
            bullish_bearish,
            stockbar_name,
            fetched_at,
            updated_at
        FROM guba_posts
        WHERE {where}
        ORDER BY publish_time DESC, post_id DESC
        """,
        params,
    ).fetchall()


def _summarize_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    label_counts: Counter[str] = Counter()
    eastmoney_counts: Counter[str] = Counter()
    positive_hits: Counter[str] = Counter()
    negative_hits: Counter[str] = Counter()
    total_clicks = 0
    total_comments = 0
    total_forwards = 0
    unique_authors: set[str] = set()
    hot_post_count = 0
    top_post_count = 0
    for row in rows:
        item = classify_title(str(row["title"] or ""))
        label_counts[item["label"]] += 1
        positive_hits.update(item["positive_hits"])
        negative_hits.update(item["negative_hits"])
        eastmoney_counts[str(row["bullish_bearish"])] += 1
        clicks = _safe_int(row["click_count"])
        comments = _safe_int(row["comment_count"])
        total_clicks += clicks
        total_comments += comments
        total_forwards += _safe_int(row["forward_count"])
        if str(row["author_id"] or ""):
            unique_authors.add(str(row["author_id"] or ""))
        if clicks >= 1000 or comments >= 20:
            hot_post_count += 1
        if _safe_int(row["top_status"]):
            top_post_count += 1
    classified = label_counts["positive"] + label_counts["negative"]
    sentiment_score = 0.0 if classified == 0 else round((label_counts["positive"] - label_counts["negative"]) / classified, 4)
    if sentiment_score > 0.15:
        sentiment_label = "positive"
    elif sentiment_score < -0.15:
        sentiment_label = "negative"
    else:
        sentiment_label = "neutral"
    return {
        "post_count": len(rows),
        "total_clicks": total_clicks,
        "total_comments": total_comments,
        "total_forwards": total_forwards,
        "unique_author_count": len(unique_authors),
        "hot_post_count": hot_post_count,
        "top_post_count": top_post_count,
        "sentiment_label": sentiment_label,
        "sentiment_score": sentiment_score,
        "lexicon_counts": {
            "positive": label_counts["positive"],
            "negative": label_counts["negative"],
            "neutral": label_counts["neutral"],
        },
        "eastmoney_bullish_bearish_counts": dict(sorted(eastmoney_counts.items())),
        "top_positive_keywords": positive_hits.most_common(10),
        "top_negative_keywords": negative_hits.most_common(10),
    }


def _top_posts(rows: list[sqlite3.Row], top_n: int) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: (_safe_int(row["comment_count"]), _safe_int(row["click_count"])), reverse=True)
    output: list[dict[str, Any]] = []
    for row in ranked[:top_n]:
        item = classify_title(str(row["title"] or ""))
        output.append(
            {
                "symbol": row["symbol"],
                "post_id": row["post_id"],
                "title": row["title"],
                "publish_time": row["publish_time"],
                "click_count": _safe_int(row["click_count"]),
                "comment_count": _safe_int(row["comment_count"]),
                "forward_count": _safe_int(row["forward_count"]),
                "lexicon_label": item["label"],
                "lexicon_score": item["score"],
                "source_url": row["source_url"],
            }
        )
    return output


def _summarize_by_symbol(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["symbol"]), []).append(row)
    summaries = []
    for symbol, symbol_rows in grouped.items():
        item = _summarize_rows(symbol_rows)
        item["symbol"] = symbol
        summaries.append(item)
    return sorted(summaries, key=lambda item: (item["post_count"], item["total_comments"], item["total_clicks"]), reverse=True)


def build_sentiment_report(config: SentimentConfig) -> dict[str, Any]:
    target_date = config.date or _today()
    _validate_report_date(target_date)
    previous_date = _previous_date(target_date)
    symbols = config.symbols or None
    if symbols:
        symbols = [str(symbol).strip().zfill(6) for symbol in symbols if str(symbol).strip()]
    universe_count = None
    try:
        universe_count = len(load_symbols())
    except Exception:
        pass
    if not config.db_path.exists():
        return {
            "ok": False,
            "date": target_date,
            "error": f"Guba SQLite not found: {config.db_path}",
            "last_run": _read_last_run(config.last_run_path),
        }
    with sqlite3.connect(config.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = _load_rows(conn, target_date, symbols)
        previous_rows = _load_rows(conn, previous_date, symbols)
    today_summary = _summarize_rows(rows)
    previous_summary = _summarize_rows(previous_rows)
    by_symbol = _summarize_by_symbol(rows)
    by_symbol_with_posts = [item for item in by_symbol if item["post_count"] > 0]
    report = {
        "ok": True,
        "date": target_date,
        "previous_date": previous_date,
        "generated_at": now_iso(),
        "db_path": str(config.db_path),
        "last_run": _read_last_run(config.last_run_path),
        "coverage": {
            "configured_symbol_count": universe_count,
            "symbols_with_posts": len(by_symbol_with_posts),
            "requested_symbol_count": len(symbols) if symbols else None,
        },
        "market_summary": today_summary,
        "previous_summary": previous_summary,
        "day_over_day_change": {
            "post_count": today_summary["post_count"] - previous_summary["post_count"],
            "total_comments": today_summary["total_comments"] - previous_summary["total_comments"],
            "total_clicks": today_summary["total_clicks"] - previous_summary["total_clicks"],
            "sentiment_score": round(today_summary["sentiment_score"] - previous_summary["sentiment_score"], 4),
        },
        "by_symbol": by_symbol[:200],
        "top_posts": _top_posts(rows, config.top_n),
        "notes": [
            "Sentiment is a lightweight title-keyword signal, not investment advice.",
            "eastmoney_bullish_bearish_counts preserves the raw public list-page field for later calibration.",
        ],
    }
    if config.write:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = config.output_dir / f"guba_sentiment_{target_date}.json"
        output_path.write_text(json.dumps(clean_json_value(report), ensure_ascii=False, indent=2), encoding="utf-8")
        report["output_path"] = str(output_path)
    return report


def _effective_run_all(config: GubaDailyConfig) -> bool:
    if config.plan_only or config.report_only or config.symbols:
        return False
    return config.run_all or config.batch_index is None


def _validate_config(config: GubaDailyConfig) -> None:
    if config.batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if config.batch_index is not None and config.batch_index < 0:
        raise ValueError("batch_index must be >= 0")
    if config.pages <= 0:
        raise ValueError("pages must be positive")
    if config.timeout <= 0:
        raise ValueError("timeout must be positive")
    if config.retries <= 0:
        raise ValueError("retries must be positive")
    if config.top_n <= 0:
        raise ValueError("top_n must be positive")
    if config.report_only and config.plan_only:
        raise ValueError("Use either report_only or plan_only, not both")
    if config.refresh_symbols not in {"auto", "always", "never"}:
        raise ValueError("refresh_symbols must be auto, always, or never")


def run_guba_daily(config: GubaDailyConfig) -> dict[str, Any]:
    """Run the production Eastmoney Guba daily interface.

    This facade intentionally exposes one stable entrypoint for agents. It always
    disables global news and Jiuyangongshe, writes to SQLite, uses Guba resume,
    and returns a machine-readable summary.
    """
    _validate_config(config)
    started_perf = time.perf_counter()
    start_date, end_date = _date_window(config)

    if config.report_only:
        payload = {
            "ok": False,
            "mode": "report_only",
            "started_at": now_iso(),
            "start_date": start_date,
            "end_date": end_date,
            "symbols": config.symbols,
            "sqlite_path": str(GUBA_DB_PATH),
            "daily_last_run_path": str(DAILY_LAST_RUN_PATH),
            "report_last_run_path": str(REPORT_LAST_RUN_PATH),
        }
        report = build_sentiment_report(
            SentimentConfig(
                date=end_date,
                symbols=config.symbols,
                output_dir=SENTIMENT_OUTPUT_DIR,
                top_n=config.top_n,
                write=not config.no_write_report,
            )
        )
        payload.update({
            "ok": bool(report.get("ok")),
            "finished_at": now_iso(),
            "duration_seconds": round(time.perf_counter() - started_perf, 3),
            "sentiment": report,
        })
        _save_json(REPORT_LAST_RUN_PATH, payload)
        return payload

    effective_run_all = _effective_run_all(config)

    symbol_refresh = None
    planned_symbols = None
    if config.plan_only and not config.symbols and config.refresh_symbols != "never":
        symbol_rows = fetch_a_share_symbols(include_bj=config.include_bj, timeout=config.timeout, retries=config.retries)
        planned_symbols = [row["symbol"] for row in symbol_rows]
        plan_source = "akshare_stock_info_a_code_name" if all(row.get("pool") == "akshare_a" for row in symbol_rows) else "eastmoney_clist_live_plan"
        symbol_refresh = {
            "source": plan_source,
            "symbol_count": len(planned_symbols),
            "include_bj": config.include_bj,
            "written": False,
            "generated_at": now_iso(),
        }
    elif _should_refresh_symbols(config):
        symbol_refresh = refresh_symbols_file(
            include_bj=config.include_bj,
            timeout=config.timeout,
            retries=config.retries,
        )

    symbols = _normalize_symbols(config.symbols or planned_symbols)
    batch_count = max(1, math.ceil(len(symbols) / config.batch_size)) if symbols else 0
    run_id, signature = _run_signature(symbols, config, start_date, end_date)
    daily_checkpoint_path = _daily_checkpoint_path(run_id)

    base_payload: dict[str, Any] = {
        "ok": False,
        "mode": "plan" if config.plan_only else "all" if effective_run_all else "symbols" if config.symbols else "batch",
        "started_at": now_iso(),
        "run_id": run_id,
        "start_date": start_date,
        "end_date": end_date,
        "pages": config.pages,
        "batch_size": config.batch_size,
        "batch_index": config.batch_index,
        "batch_count": batch_count,
        "symbol_count": len(symbols),
        "symbols_path": str(DEFAULT_SYMBOLS_PATH),
        "sqlite_path": str(GUBA_DB_PATH),
        "daily_checkpoint_path": str(daily_checkpoint_path),
        "daily_last_run_path": str(DAILY_LAST_RUN_PATH),
        "symbol_refresh": symbol_refresh,
        "coverage_note": "Eastmoney Guba public list pages only; increase pages for highly active symbols.",
    }

    if config.plan_only:
        base_payload["ok"] = True
        base_payload["finished_at"] = now_iso()
        base_payload["duration_seconds"] = round(time.perf_counter() - started_perf, 3)
        base_payload["batches"] = [
            {
                "batch_index": index,
                "selected_symbol_count": _batch_symbol_count(len(symbols), config.batch_size, index),
            }
            for index in range(batch_count)
        ]
        return base_payload

    if not symbols:
        base_payload["error"] = "No symbols to process"
        base_payload["finished_at"] = now_iso()
        base_payload["duration_seconds"] = round(time.perf_counter() - started_perf, 3)
        _save_json(DAILY_LAST_RUN_PATH, base_payload)
        return base_payload

    if config.symbols:
        target_batches = [0]
        effective_batch_size = len(symbols)
    elif effective_run_all:
        target_batches = list(range(batch_count))
        effective_batch_size = config.batch_size
    else:
        batch_index = 0 if config.batch_index is None else config.batch_index
        if batch_index >= batch_count:
            raise ValueError(f"batch_index {batch_index} out of range; batch_count={batch_count}")
        target_batches = [batch_index]
        effective_batch_size = config.batch_size

    daily_checkpoint = _load_daily_checkpoint(daily_checkpoint_path, signature, config.reset) if effective_run_all else None
    batches: list[dict[str, Any]] = []

    for batch_index in target_batches:
        if daily_checkpoint and daily_checkpoint.get("completed_batches", {}).get(str(batch_index)):
            batches.append(
                {
                    "batch_index": batch_index,
                    "batch_size": effective_batch_size,
                    "selected_symbol_count": _batch_symbol_count(len(symbols), effective_batch_size, batch_index),
                    "ok": True,
                    "skipped_by_daily_checkpoint": True,
                }
            )
            continue

        results = update_forum(
            symbols,
            timeout=config.timeout,
            retries=config.retries,
            guba_pages=config.pages,
            forum_start_date=start_date,
            forum_end_date=end_date,
            write_guba_db=True,
            include_jiuyangongshe=False,
            resume_guba=True,
            reset_guba_checkpoint=config.reset,
            batch_size=effective_batch_size,
            batch_index=batch_index,
        )
        batch_summary = _summarize_batch(batch_index, effective_batch_size, len(symbols), results)
        batches.append(batch_summary)

        if daily_checkpoint is not None:
            key = str(batch_index)
            if batch_summary["ok"]:
                daily_checkpoint["completed_batches"][key] = {
                    "finished_at": now_iso(),
                    "record_count": batch_summary["record_count"],
                    "selected_symbol_count": batch_summary["selected_symbol_count"],
                }
                daily_checkpoint["failed_batches"].pop(key, None)
            else:
                daily_checkpoint["failed_batches"][key] = {
                    "finished_at": now_iso(),
                    "failed_symbols": batch_summary["failed_symbols"],
                }
            daily_checkpoint["updated_at"] = now_iso()
            _save_json(daily_checkpoint_path, daily_checkpoint)

    all_ok = bool(batches) and all(batch.get("ok") for batch in batches)
    base_payload.update(
        {
            "ok": all_ok,
            "finished_at": now_iso(),
            "duration_seconds": round(time.perf_counter() - started_perf, 3),
            "batches": batches,
            "record_count": sum(int(batch.get("record_count") or 0) for batch in batches),
            "failed_batch_indexes": [batch.get("batch_index") for batch in batches if not batch.get("ok")],
        }
    )

    if effective_run_all and daily_checkpoint is not None:
        completed = daily_checkpoint.get("completed_batches", {})
        base_payload["daily_checkpoint_completed_batches"] = len(completed)
        if len(completed) >= batch_count and all_ok:
            try:
                daily_checkpoint_path.unlink()
                base_payload["daily_checkpoint_cleared"] = True
            except FileNotFoundError:
                base_payload["daily_checkpoint_cleared"] = True
        else:
            base_payload["daily_checkpoint_cleared"] = False

    _save_json(DAILY_LAST_RUN_PATH, base_payload)
    if not config.no_report and (all_ok or config.report_on_partial):
        report = build_sentiment_report(
            SentimentConfig(
                date=end_date,
                symbols=config.symbols,
                output_dir=SENTIMENT_OUTPUT_DIR,
                top_n=config.top_n,
                write=not config.no_write_report,
            )
        )
        base_payload["sentiment"] = report
        base_payload["ok"] = bool(base_payload.get("ok") and report.get("ok"))
        if not report.get("ok"):
            base_payload["error"] = "sentiment report failed"
    elif not all_ok:
        base_payload["report_skipped_reason"] = "collection failed; rerun the same command to resume, or pass --report-on-partial"
    base_payload["finished_at"] = now_iso()
    base_payload["duration_seconds"] = round(time.perf_counter() - started_perf, 3)
    _save_json(DAILY_LAST_RUN_PATH, base_payload)
    return base_payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable production facade for Eastmoney Guba daily collection.")
    parser.add_argument("symbols", nargs="*", help="Optional explicit A-share symbols. If provided, batch slicing is bypassed.")
    parser.add_argument("--all", action="store_true", help="Run every batch in sequence. This is also the default when no symbols are provided.")
    parser.add_argument("--plan", action="store_true", help="Print the current batch plan without network writes.")
    parser.add_argument("--report-only", action="store_true", help="Only build the daily sentiment report from existing SQLite data.")
    parser.add_argument("--batch-index", "--batch", dest="batch_index", type=int, default=None, help="Zero-based batch index for one-batch runs.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Symbols per batch. Default: 500.")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="Eastmoney Guba list pages per symbol. Default: 3.")
    parser.add_argument("--date", help="Daily target date YYYY-MM-DD. Defaults to local today.")
    parser.add_argument("--start-date", help="Backfill start date YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Backfill end date YYYY-MM-DD.")
    parser.add_argument(
        "--refresh-symbols",
        nargs="?",
        choices=["auto", "always", "never"],
        const="always",
        default="auto",
        help="Refresh all-A symbols: auto, always, or never. Default: auto.",
    )
    parser.add_argument("--exclude-bj", action="store_true", help="Exclude Beijing Stock Exchange listed symbols when refreshing.")
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--reset", action="store_true", help="Reset matching Guba checkpoints before running. Do not use for normal resume.")
    parser.add_argument("--no-report", action="store_true", help="Collect posts only; do not build the sentiment report.")
    parser.add_argument("--report-on-partial", action="store_true", help="Build report even if some batches failed.")
    parser.add_argument("--no-write-report", action="store_true", help="Print report in stdout but do not write data/state/guba_sentiment JSON.")
    parser.add_argument("--top-n", type=int, default=20, help="Top sentiment posts to include. Default: 20.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    config = GubaDailyConfig(
        symbols=args.symbols or None,
        run_all=args.all,
        plan_only=args.plan,
        report_only=args.report_only,
        batch_index=args.batch_index,
        batch_size=args.batch_size,
        pages=args.pages,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        refresh_symbols=args.refresh_symbols,
        include_bj=not args.exclude_bj,
        timeout=args.timeout,
        retries=args.retries,
        reset=args.reset,
        no_report=args.no_report,
        report_on_partial=args.report_on_partial,
        no_write_report=args.no_write_report,
        top_n=args.top_n,
    )
    try:
        payload = run_guba_daily(config)
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "finished_at": now_iso(),
        }
        emit_json(payload)
        return 1
    emit_json(payload)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
