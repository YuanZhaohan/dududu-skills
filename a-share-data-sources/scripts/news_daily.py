from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import DEFAULT_NEWS_SOURCES_PATH, STATE_DIR, clean_json_value, emit_json, load_yaml, now_iso  # noqa: E402
from news_data import summarize_result, update_global_news  # noqa: E402
from sources.news_pool import CHECKPOINT_PATH  # noqa: E402


NEWS_LAST_RUN_PATH = STATE_DIR / "news_daily_last_run.json"


@dataclass
class NewsDailyConfig:
    plan_only: bool = False
    lookback_hours: int = 24
    timeout: int = 5
    retries: int = 2
    deadline: int = 0
    max_workers: int = 4
    reset: bool = False
    no_resume: bool = False


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_json_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("id") or source.get("name") or "unknown")


def _build_plan(config: NewsDailyConfig) -> dict[str, Any]:
    config_data = load_yaml(DEFAULT_NEWS_SOURCES_PATH)
    rss_sources = list(config_data.get("rss_sources") or [])
    api_sources = list(config_data.get("api_sources") or [])
    api_type_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for source in [*rss_sources, *api_sources]:
        category = str(source.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1
    for source in api_sources:
        source_type = str(source.get("type") or "json")
        api_type_counts[source_type] = api_type_counts.get(source_type, 0) + 1
    return {
        "ok": True,
        "mode": "plan",
        "started_at": now_iso(),
        "finished_at": now_iso(),
        "config_path": str(DEFAULT_NEWS_SOURCES_PATH),
        "checkpoint_path": str(CHECKPOINT_PATH),
        "last_run_path": str(NEWS_LAST_RUN_PATH),
        "lookback_hours": config.lookback_hours,
        "timeout": config.timeout,
        "retries": config.retries,
        "deadline": config.deadline,
        "max_workers": config.max_workers,
        "resume": not config.no_resume,
        "source_count": len(rss_sources) + len(api_sources),
        "rss_source_count": len(rss_sources),
        "api_source_count": len(api_sources),
        "api_type_counts": dict(sorted(api_type_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "source_ids": [_source_id(source) for source in [*rss_sources, *api_sources]],
    }


def run_news_daily(config: NewsDailyConfig) -> dict[str, Any]:
    if config.lookback_hours <= 0:
        raise ValueError("lookback_hours must be positive")
    if config.timeout <= 0:
        raise ValueError("timeout must be positive")
    if config.retries <= 0:
        raise ValueError("retries must be positive")
    if config.max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if config.plan_only:
        return _build_plan(config)
    payload: dict[str, Any] = {
        "ok": False,
        "mode": "global_news",
        "started_at": now_iso(),
        "lookback_hours": config.lookback_hours,
        "timeout": config.timeout,
        "retries": config.retries,
        "deadline": config.deadline,
        "max_workers": config.max_workers,
        "checkpoint_path": str(CHECKPOINT_PATH),
        "last_run_path": str(NEWS_LAST_RUN_PATH),
    }
    result = update_global_news(
        lookback_hours=config.lookback_hours,
        timeout=config.timeout,
        retries=config.retries,
        deadline_seconds=config.deadline or None,
        max_workers=config.max_workers,
        resume=not config.no_resume,
        reset_checkpoint=config.reset,
    )
    summary = summarize_result(result)
    payload.update(
        {
            "ok": bool(result.ok),
            "finished_at": now_iso(),
            "record_count": summary["record_count"],
            "result": summary,
        }
    )
    _save_json(NEWS_LAST_RUN_PATH, payload)
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable global news daily facade.")
    parser.add_argument("--plan", action="store_true", help="Print source plan without fetching data.")
    parser.add_argument("--lookback-hours", type=int, default=24, help="News lookback window in hours. Default: 24.")
    parser.add_argument("--timeout", type=int, default=5, help="Per-source request timeout seconds. Default: 5.")
    parser.add_argument("--retries", type=int, default=2, help="Per-source retry count. Default: 2.")
    parser.add_argument("--deadline", type=int, default=0, help="Overall deadline seconds; 0 waits for bounded retries.")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel source workers. Default: 4.")
    parser.add_argument("--reset", action="store_true", help="Reset news checkpoint before fetching. Do not use for normal resume.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore news checkpoint. Do not use for normal resume.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        payload = run_news_daily(
            NewsDailyConfig(
                plan_only=args.plan,
                lookback_hours=args.lookback_hours,
                timeout=args.timeout,
                retries=args.retries,
                deadline=args.deadline,
                max_workers=args.max_workers,
                reset=args.reset,
                no_resume=args.no_resume,
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