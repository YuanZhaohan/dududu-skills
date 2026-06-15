from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    FetchResult,
    NORMALIZED_DIR,
    SourceAttempt,
    append_jsonl_records,
    catalog_attempt,
    emit_json,
    load_symbols,
    normalize_symbol,
    now_iso,
    result_to_dict,
    write_raw_payload,
)
from sources.eastmoney_guba import SOURCE as GUBA_SOURCE, fetch_posts  # noqa: E402
from sources.jiuyangongshe import SOURCE as JIUYANGONGSHE_SOURCE, fetch_mentions  # noqa: E402
from sources.news_pool import SOURCE as NEWS_SOURCE, fetch_all_news_with_report  # noqa: E402


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
    return {
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


def update_forum(symbols: list[str] | None = None, *, timeout: int = 5, retries: int = 1) -> list[FetchResult]:
    symbols = symbols or load_symbols()
    results: list[FetchResult] = []
    for symbol in symbols:
        plain = normalize_symbol(symbol)["plain"]
        started = now_iso()
        try:
            records = fetch_posts(plain, timeout=timeout, retries=retries)
            raw_path = write_raw_payload("forum", GUBA_SOURCE, plain, {"records": records})
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
        catalog_attempt(result)
        results.append(result)
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
        results.append(result)
    return results


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
) -> list[FetchResult]:
    results = [
        update_global_news(
            lookback_hours=lookback_hours,
            timeout=timeout,
            retries=retries,
            deadline_seconds=deadline_seconds,
            max_workers=max_workers,
            resume=resume,
            reset_checkpoint=reset_checkpoint,
        )
    ]
    if include_forum:
        results.extend(update_forum(symbols, timeout=timeout, retries=retries))
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch global financial news and optional Eastmoney Guba posts.")
    parser.add_argument("symbols", nargs="*", help="A-share symbols for forum data, e.g. 600519 000001")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=5, help="Per-source request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Per-source retry count")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel news source workers; use 1 for strict source order")
    parser.add_argument("--deadline", type=int, default=0, help="Overall news pool deadline in seconds; 0 waits for bounded retries")
    parser.add_argument("--no-resume", action="store_true", help="Ignore checkpoint and refetch all news sources")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete any existing checkpoint before running")
    parser.add_argument("--skip-forum", action="store_true", help="Only fetch global news pool")
    parser.add_argument("--full", action="store_true", help="Print full records instead of a concise summary")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
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
    )
    emit_json([result_to_dict(r) for r in results] if args.full else [summarize_result(r) for r in results])
