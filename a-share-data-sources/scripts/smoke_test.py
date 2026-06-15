from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from announcements import update_announcements  # noqa: E402
from common import FetchResult, emit_json, load_symbols  # noqa: E402
from financial_data import update_financial  # noqa: E402
from market_data import update_market  # noqa: E402
from news_data import update_news  # noqa: E402


def summarize_result(result: FetchResult) -> dict[str, Any]:
    failed_attempts = []
    for attempt in result.attempts or []:
        if not attempt.ok:
            failed_attempts.append(
                {
                    "section": attempt.section,
                    "source": attempt.source,
                    "symbol": attempt.symbol,
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
        "failed_attempts": failed_attempts,
    }


def run_step(name: str, func: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = func()
        if isinstance(value, list):
            items = [summarize_result(item) for item in value]
            return {
                "ok": all(item["ok"] for item in items),
                "record_count": sum(int(item["record_count"]) for item in items),
                "items": items,
            }
        if isinstance(value, FetchResult):
            return summarize_result(value)
        return {"ok": True, "value": value}
    except Exception as exc:
        return {"ok": False, "record_count": 0, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "step": name}


def main(args: list[str]) -> dict[str, Any]:
    symbols = args or load_symbols()
    return {
        "symbols": symbols,
        "market": run_step("market", lambda: update_market(symbols)),
        "financial": run_step("financial", lambda: update_financial(symbols)),
        "announcements": run_step("announcements", lambda: update_announcements(symbols)),
        "news": run_step("news", lambda: update_news(symbols)),
    }


if __name__ == "__main__":
    emit_json(main(sys.argv[1:]))
