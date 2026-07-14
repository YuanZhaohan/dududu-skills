from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    FetchResult,
    NORMALIZED_DIR,
    STATE_DIR,
    SourceAttempt,
    append_jsonl_records,
    catalog_attempt,
    clean_json_value,
    emit_json,
    load_symbols,
    normalize_symbol,
    now_iso,
    result_to_dict,
    stable_hash,
    write_raw_payload,
)
from sources.eastmoney_guba import (  # noqa: E402
    GUBA_DB_PATH,
    SOURCE as GUBA_SOURCE,
    fetch_posts,
    refresh_guba_daily_stats,
    refresh_symbols_file,
    upsert_guba_posts,
)
from sources.jiuyangongshe import SOURCE as JIUYANGONGSHE_SOURCE, fetch_mentions  # noqa: E402
from sources.news_pool import SOURCE as NEWS_SOURCE, fetch_all_news_with_report  # noqa: E402


GUBA_CHECKPOINT_PATH = STATE_DIR / "eastmoney_guba_checkpoint.json"


def _attempts_from_news_report(report: dict[str, Any]) -> list[SourceAttempt]:
    attempts: list[SourceAttempt] = []
    for item in report.get("sources", []):
        attempts.append(
            SourceAttempt(
                "news",
                str(item.get("source_id", NEWS_SOURCE)),
                "GLOBAL",
                bool(item.get("provider_ok")),
                int(item.get("record_count") or 0),
                item.get("error"),
                str(item.get("started_at") or ""),
                str(item.get("finished_at") or ""),
            )
        )
    return attempts


def summarize_result(result: FetchResult) -> dict[str, Any]:
    failed_attempts = []
    for attempt in result.attempts or []:
        if not attempt.ok:
            failed_attempts.append(
                {
                    "section": attempt.section,
                    "source": attempt.source,
                    "symbol": attempt.symbol,
                    "record_count": attempt.record_count,
                    "error": attempt.error,
                }
            )
    summary = {
        "section": result.section,
        "source": result.source,
        "symbol": result.symbol,
        "ok": result.ok,
        "record_count": len(result.records),
        "raw_path": result.raw_path,
        "normalized_path": result.normalized_path,
        "error": result.error,
        "failed_attempt_count": len(failed_attempts),
        "failed_attempts": failed_attempts[:12],
    }
    if result.source == GUBA_SOURCE:
        summary["sqlite_path"] = str(GUBA_DB_PATH)
    return summary


def _selected_symbols(symbols: list[str], batch_size: int | None, batch_index: int) -> list[str]:
    normalized = [normalize_symbol(symbol)["plain"] for symbol in symbols]
    if not batch_size:
        return normalized
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if batch_index < 0:
        raise ValueError("batch_index must be >= 0")
    start = batch_index * batch_size
    return normalized[start : start + batch_size]


def _guba_checkpoint_path(batch_size: int | None, batch_index: int) -> Path:
    if batch_size:
        return STATE_DIR / f"eastmoney_guba_checkpoint_b{batch_size}_{batch_index}.json"
    return GUBA_CHECKPOINT_PATH


def _guba_checkpoint_signature(
    symbols: list[str],
    *,
    guba_pages: int,
    forum_start_date: str | None,
    forum_end_date: str | None,
    write_guba_db: bool,
) -> str:
    payload = {
        "source": GUBA_SOURCE,
        "symbols": symbols,
        "guba_pages": guba_pages,
        "forum_start_date": forum_start_date,
        "forum_end_date": forum_end_date,
        "write_guba_db": write_guba_db,
    }
    return stable_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True), 24)


def _new_guba_checkpoint(
    signature: str,
    symbols: list[str],
    *,
    guba_pages: int,
    forum_start_date: str | None,
    forum_end_date: str | None,
    write_guba_db: bool,
    batch_size: int | None,
    batch_index: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "source": GUBA_SOURCE,
        "signature": signature,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "symbol_count": len(symbols),
        "symbols": symbols,
        "params": {
            "guba_pages": guba_pages,
            "forum_start_date": forum_start_date,
            "forum_end_date": forum_end_date,
            "write_guba_db": write_guba_db,
            "batch_size": batch_size,
            "batch_index": batch_index,
        },
        "completed": {},
        "failed": {},
    }


def _load_guba_checkpoint(path: Path, signature: str, factory) -> dict[str, Any]:
    if not path.exists():
        return factory()
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return factory()
    if checkpoint.get("signature") != signature:
        return factory()
    checkpoint.setdefault("completed", {})
    checkpoint.setdefault("failed", {})
    return checkpoint


def _save_guba_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint["updated_at"] = now_iso()
    path.write_text(json.dumps(clean_json_value(checkpoint), ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_guba_checkpoint(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def update_global_news(
    *,
    lookback_hours: int = 24,
    timeout: int = 5,
    retries: int = 2,
    deadline_seconds: int | None = None,
    max_workers: int = 4,
    resume: bool = True,
    reset_checkpoint: bool = False,
) -> FetchResult:
    started = now_iso()
    try:
        records, report = fetch_all_news_with_report(
            lookback_hours=lookback_hours,
            timeout=timeout,
            retries=retries,
            deadline_seconds=deadline_seconds,
            max_workers=max_workers,
            resume=resume,
            reset_checkpoint=reset_checkpoint,
        )
        raw_path = write_raw_payload("news", NEWS_SOURCE, "GLOBAL", {"records": records, "report": report})
        normalized_path, _ = append_jsonl_records(NORMALIZED_DIR / "news" / "GLOBAL.jsonl", records)
        ok = len(records) > 0
        attempts = _attempts_from_news_report(report)
        attempts.append(
            SourceAttempt(
                "news",
                NEWS_SOURCE,
                "GLOBAL",
                ok,
                len(records),
                None if ok else "news_pool returned no records",
                started,
                now_iso(),
            )
        )
        result = FetchResult(
            "news",
            NEWS_SOURCE,
            "GLOBAL",
            ok,
            records,
            raw_path=str(raw_path),
            normalized_path=str(normalized_path),
            error=None if ok else "news_pool returned no records",
            attempts=attempts,
        )
    except Exception as exc:
        result = FetchResult(
            "news",
            NEWS_SOURCE,
            "GLOBAL",
            False,
            [],
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            attempts=[SourceAttempt("news", NEWS_SOURCE, "GLOBAL", False, 0, str(exc)[:200], started, now_iso())],
        )
    catalog_attempt(result)
    return result


def update_forum(
    symbols: list[str] | None = None,
    *,
    timeout: int = 5,
    retries: int = 1,
    guba_pages: int = 2,
    forum_start_date: str | None = None,
    forum_end_date: str | None = None,
    write_guba_db: bool = True,
    include_jiuyangongshe: bool = True,
    resume_guba: bool = True,
    reset_guba_checkpoint: bool = False,
    batch_size: int | None = None,
    batch_index: int = 0,
) -> list[FetchResult]:
    selected = _selected_symbols(symbols or load_symbols(), batch_size, batch_index)
    checkpoint_path = _guba_checkpoint_path(batch_size, batch_index)
    signature = _guba_checkpoint_signature(
        selected,
        guba_pages=guba_pages,
        forum_start_date=forum_start_date,
        forum_end_date=forum_end_date,
        write_guba_db=write_guba_db,
    )
    if reset_guba_checkpoint:
        _clear_guba_checkpoint(checkpoint_path)
    checkpoint_factory = lambda: _new_guba_checkpoint(
        signature,
        selected,
        guba_pages=guba_pages,
        forum_start_date=forum_start_date,
        forum_end_date=forum_end_date,
        write_guba_db=write_guba_db,
        batch_size=batch_size,
        batch_index=batch_index,
    )
    checkpoint = _load_guba_checkpoint(checkpoint_path, signature, checkpoint_factory) if resume_guba else checkpoint_factory()
    completed = checkpoint.setdefault("completed", {})
    failed = checkpoint.setdefault("failed", {})

    results: list[FetchResult] = []
    for plain in selected:
        if resume_guba and completed.get(plain, {}).get("ok"):
            if include_jiuyangongshe:
                results.extend(_update_jiuyangongshe(plain, timeout=timeout, retries=retries))
            continue

        started = now_iso()
        try:
            records = fetch_posts(
                plain,
                pages=guba_pages,
                start_date=forum_start_date,
                end_date=forum_end_date,
                timeout=timeout,
                retries=retries,
            )
            db_rows = upsert_guba_posts(records) if write_guba_db else 0
            stats_rows = (
                refresh_guba_daily_stats(plain, start_date=forum_start_date, end_date=forum_end_date)
                if write_guba_db
                else 0
            )
            raw_path = write_raw_payload(
                "forum",
                GUBA_SOURCE,
                plain,
                {
                    "records": records,
                    "sqlite_path": str(GUBA_DB_PATH) if write_guba_db else "",
                    "sqlite_upsert_rows": db_rows,
                    "daily_stats_rows": stats_rows,
                    "pages": guba_pages,
                    "start_date": forum_start_date,
                    "end_date": forum_end_date,
                    "checkpoint_path": str(checkpoint_path) if resume_guba else "",
                    "batch_size": batch_size,
                    "batch_index": batch_index,
                },
            )
            normalized_path, _ = append_jsonl_records(NORMALIZED_DIR / "forum" / f"{plain}.jsonl", records)
            result = FetchResult(
                "forum",
                GUBA_SOURCE,
                plain,
                True,
                records,
                raw_path=str(raw_path),
                normalized_path=str(normalized_path),
                attempts=[SourceAttempt("forum", GUBA_SOURCE, plain, True, len(records), None, started, now_iso())],
            )
            if resume_guba:
                completed[plain] = {
                    "ok": True,
                    "record_count": len(records),
                    "finished_at": now_iso(),
                    "raw_path": str(raw_path),
                    "normalized_path": str(normalized_path),
                }
                failed.pop(plain, None)
                _save_guba_checkpoint(checkpoint_path, checkpoint)
        except Exception as exc:
            result = FetchResult(
                "forum",
                GUBA_SOURCE,
                plain,
                False,
                [],
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
                attempts=[SourceAttempt("forum", GUBA_SOURCE, plain, False, 0, str(exc)[:200], started, now_iso())],
            )
            if resume_guba:
                failed[plain] = {"error": result.error, "finished_at": now_iso()}
                _save_guba_checkpoint(checkpoint_path, checkpoint)
        catalog_attempt(result)
        results.append(result)
        if include_jiuyangongshe:
            results.extend(_update_jiuyangongshe(plain, timeout=timeout, retries=retries))

    if resume_guba and selected and all(completed.get(symbol, {}).get("ok") for symbol in selected):
        _clear_guba_checkpoint(checkpoint_path)
    return results


def _update_jiuyangongshe(plain: str, *, timeout: int, retries: int) -> list[FetchResult]:
    started = now_iso()
    try:
        records = fetch_mentions(plain, timeout=timeout, retries=retries)
        raw_path = write_raw_payload("forum", JIUYANGONGSHE_SOURCE, plain, {"records": records})
        normalized_path, _ = append_jsonl_records(NORMALIZED_DIR / "forum" / f"{plain}.jsonl", records)
        result = FetchResult(
            "forum",
            JIUYANGONGSHE_SOURCE,
            plain,
            True,
            records,
            raw_path=str(raw_path),
            normalized_path=str(normalized_path),
            attempts=[SourceAttempt("forum", JIUYANGONGSHE_SOURCE, plain, True, len(records), None, started, now_iso())],
        )
    except Exception as exc:
        result = FetchResult(
            "forum",
            JIUYANGONGSHE_SOURCE,
            plain,
            False,
            [],
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            attempts=[SourceAttempt("forum", JIUYANGONGSHE_SOURCE, plain, False, 0, str(exc)[:200], started, now_iso())],
        )
    catalog_attempt(result)
    return [result]


def update_news(
    symbols: list[str] | None = None,
    *,
    lookback_hours: int = 24,
    timeout: int = 5,
    retries: int = 2,
    deadline_seconds: int | None = None,
    max_workers: int = 4,
    resume: bool = True,
    reset_checkpoint: bool = False,
    include_forum: bool = True,
    include_global_news: bool = True,
    guba_pages: int = 2,
    forum_start_date: str | None = None,
    forum_end_date: str | None = None,
    write_guba_db: bool = True,
    include_jiuyangongshe: bool = True,
    resume_guba: bool = True,
    reset_guba_checkpoint: bool = False,
    batch_size: int | None = None,
    batch_index: int = 0,
) -> list[FetchResult]:
    results: list[FetchResult] = []
    if include_global_news:
        results.append(
            update_global_news(
                lookback_hours=lookback_hours,
                timeout=timeout,
                retries=retries,
                deadline_seconds=deadline_seconds,
                max_workers=max_workers,
                resume=resume,
                reset_checkpoint=reset_checkpoint,
            )
        )
    if include_forum:
        results.extend(
            update_forum(
                symbols,
                timeout=timeout,
                retries=retries,
                guba_pages=guba_pages,
                forum_start_date=forum_start_date,
                forum_end_date=forum_end_date,
                write_guba_db=write_guba_db,
                include_jiuyangongshe=include_jiuyangongshe,
                resume_guba=resume_guba,
                reset_guba_checkpoint=reset_guba_checkpoint,
                batch_size=batch_size,
                batch_index=batch_index,
            )
        )
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch global financial news and optional Eastmoney Guba posts.")
    parser.add_argument("symbols", nargs="*", help="A-share symbols for forum data, e.g. 600519 000001")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=5, help="Per-source request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Per-source retry count")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel news source workers; use 1 for strict source order")
    parser.add_argument("--deadline", type=int, default=0, help="Overall news pool deadline in seconds; 0 waits for bounded retries")
    parser.add_argument("--no-resume", action="store_true", help="Ignore news-pool checkpoint and refetch all news sources")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete any existing news-pool checkpoint before running")
    parser.add_argument("--skip-forum", action="store_true", help="Only fetch global news pool")
    parser.add_argument("--skip-global-news", action="store_true", help="Only fetch forum/community sources")
    parser.add_argument("--forum-pages", type=int, default=2, help="Eastmoney Guba list pages per symbol")
    parser.add_argument("--forum-start-date", help="Eastmoney Guba inclusive start date, YYYY-MM-DD")
    parser.add_argument("--forum-end-date", help="Eastmoney Guba inclusive end date, YYYY-MM-DD")
    parser.add_argument("--skip-guba-db", action="store_true", help="Do not upsert Eastmoney Guba records into SQLite")
    parser.add_argument("--skip-jiuyangongshe", action="store_true", help="Skip Jiuyangongshe forum search")
    parser.add_argument("--refresh-symbols", action="store_true", help="Refresh data/input/symbols.txt from Eastmoney A-share universe before running")
    parser.add_argument("--exclude-bj", action="store_true", help="When refreshing symbols, exclude Beijing Stock Exchange symbols")
    parser.add_argument("--batch-size", type=int, default=0, help="Process only this many symbols from the selected symbol list")
    parser.add_argument("--batch-index", type=int, default=0, help="Zero-based batch index used with --batch-size")
    parser.add_argument("--no-guba-resume", action="store_true", help="Ignore Eastmoney Guba per-symbol checkpoint")
    parser.add_argument("--reset-guba-checkpoint", action="store_true", help="Delete Eastmoney Guba checkpoint before running")
    parser.add_argument("--full", action="store_true", help="Print full records instead of a concise summary")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    symbol_refresh = None
    if args.refresh_symbols:
        symbol_refresh = refresh_symbols_file(include_bj=not args.exclude_bj, timeout=args.timeout, retries=args.retries)
    results = update_news(
        args.symbols or None,
        lookback_hours=args.lookback_hours,
        timeout=args.timeout,
        retries=args.retries,
        deadline_seconds=args.deadline or None,
        max_workers=args.max_workers,
        resume=not args.no_resume,
        reset_checkpoint=args.reset_checkpoint,
        include_forum=not args.skip_forum,
        include_global_news=not args.skip_global_news,
        guba_pages=args.forum_pages,
        forum_start_date=args.forum_start_date,
        forum_end_date=args.forum_end_date,
        write_guba_db=not args.skip_guba_db,
        include_jiuyangongshe=not args.skip_jiuyangongshe,
        resume_guba=not args.no_guba_resume,
        reset_guba_checkpoint=args.reset_guba_checkpoint,
        batch_size=args.batch_size or None,
        batch_index=args.batch_index,
    )
    payload = [result_to_dict(r) for r in results] if args.full else [summarize_result(r) for r in results]
    if symbol_refresh:
        emit_json({"symbol_refresh": symbol_refresh, "results": payload})
    else:
        emit_json(payload)
