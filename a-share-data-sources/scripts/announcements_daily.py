from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from announcements import update_announcements  # noqa: E402
from common import STATE_DIR, emit_json, now_iso  # noqa: E402
from daily_utils import batch_count, build_batches, resolve_symbols, save_json, slice_batch  # noqa: E402


ANNOUNCEMENTS_LAST_RUN_PATH = STATE_DIR / "announcements_daily_last_run.json"
DEFAULT_BATCH_SIZE = 100
DEFAULT_PAGE_SIZE = 30


@dataclass
class AnnouncementsDailyConfig:
    symbols: list[str] | None = None
    plan_only: bool = False
    batch_index: int = 0
    batch_size: int = DEFAULT_BATCH_SIZE
    page_size: int = DEFAULT_PAGE_SIZE
    refresh_symbols: str = "auto"
    include_bj: bool = True
    timeout: int = 8
    retries: int = 2


def _summarize_result(result) -> dict[str, Any]:
    return {
        "section": result.section,
        "source": result.source,
        "symbol": result.symbol,
        "ok": result.ok,
        "record_count": len(result.records),
        "raw_path": result.raw_path,
        "normalized_path": result.normalized_path,
        "error": result.error,
    }


def run_announcements_daily(config: AnnouncementsDailyConfig) -> dict[str, Any]:
    symbols, symbol_refresh = resolve_symbols(
        explicit_symbols=config.symbols,
        plan_only=config.plan_only,
        refresh_symbols=config.refresh_symbols,
        include_bj=config.include_bj,
        timeout=config.timeout,
        retries=config.retries,
    )
    selected = symbols if config.symbols else slice_batch(symbols, config.batch_size, config.batch_index)
    payload: dict[str, Any] = {
        "ok": False,
        "mode": "plan" if config.plan_only else "symbols" if config.symbols else "batch",
        "started_at": now_iso(),
        "symbol_count": len(symbols),
        "selected_symbol_count": len(selected),
        "batch_size": config.batch_size,
        "batch_index": config.batch_index,
        "batch_count": batch_count(symbols, config.batch_size),
        "page_size": config.page_size,
        "symbol_refresh": symbol_refresh,
        "last_run_path": str(ANNOUNCEMENTS_LAST_RUN_PATH),
    }
    if config.plan_only:
        payload["ok"] = True
        payload["finished_at"] = now_iso()
        payload["batches"] = build_batches(symbols, config.batch_size)
        return payload
    if not selected:
        payload["error"] = "No symbols selected"
        payload["finished_at"] = now_iso()
        save_json(ANNOUNCEMENTS_LAST_RUN_PATH, payload)
        return payload
    results = update_announcements(selected, page_size=config.page_size)
    summaries = [_summarize_result(result) for result in results]
    payload.update(
        {
            "ok": bool(summaries) and all(item["ok"] for item in summaries),
            "finished_at": now_iso(),
            "record_count": sum(int(item["record_count"]) for item in summaries),
            "failed_symbols": [item["symbol"] for item in summaries if not item["ok"]],
            "results": summaries,
        }
    )
    save_json(ANNOUNCEMENTS_LAST_RUN_PATH, payload)
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable announcements daily facade.")
    parser.add_argument("symbols", nargs="*", help="Optional symbols; bypass batch slicing when provided.")
    parser.add_argument("--plan", action="store_true", help="Print batch plan without fetching data.")
    parser.add_argument("--batch-index", "--batch", dest="batch_index", type=int, default=0, help="Zero-based batch index.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Symbols per batch. Default: 100.")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="CNINFO page size. Default: 30.")
    parser.add_argument("--refresh-symbols", nargs="?", choices=["auto", "always", "never"], const="always", default="auto", help="Refresh all-A symbols: auto, always, or never.")
    parser.add_argument("--exclude-bj", action="store_true", help="Exclude Beijing Stock Exchange symbols when refreshing.")
    parser.add_argument("--timeout", type=int, default=8, help="Symbol-pool request timeout seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Symbol-pool retry count.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        payload = run_announcements_daily(
            AnnouncementsDailyConfig(
                symbols=args.symbols or None,
                plan_only=args.plan,
                batch_index=args.batch_index,
                batch_size=args.batch_size,
                page_size=args.page_size,
                refresh_symbols=args.refresh_symbols,
                include_bj=not args.exclude_bj,
                timeout=args.timeout,
                retries=args.retries,
            )
        )
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "finished_at": now_iso()}
        emit_json(payload)
        return 1
    emit_json(payload)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())