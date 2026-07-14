from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
from typing import Any
import time
from urllib.parse import urlparse

from common import (
    DATA_DIR,
    DEFAULT_NEWS_SOURCES_PATH,
    STATE_DIR,
    http_get,
    load_yaml,
    make_record_key,
    now_iso,
    read_json_gz,
    write_json_gz,
)


SOURCE = "news_pool"
CACHE_DIR = DATA_DIR / "raw" / "news_cache"
CHECKPOINT_PATH = STATE_DIR / "news_pool_checkpoint.json"
NEWSNOW_DEFAULT_API_URL = "https://newsnow.busiyi.world/api/s"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except Exception:
            return None
    text = str(value).strip()
    for parser in (
        lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")).replace(tzinfo=None),
        parsedate_to_datetime,
    ):
        try:
            parsed = parser(text)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except Exception:
            continue
    return None


def _cache_path(source_id: str) -> Path:
    return CACHE_DIR / f"{source_id}.json.gz"


def _read_cache(source_id: str) -> list[dict[str, Any]]:
    path = _cache_path(source_id)
    if not path.exists():
        return []
    try:
        return read_json_gz(path).get("records", [])
    except Exception:
        return []


def _write_cache(source_id: str, records: list[dict[str, Any]]) -> None:
    write_json_gz(_cache_path(source_id), {"fetched_at": now_iso(), "records": records})


def _load_checkpoint(
    checkpoint_path: Path,
    *,
    config_path: Path,
    lookback_hours: int,
    max_age_hours: int,
) -> dict[str, Any]:
    if not checkpoint_path.exists():
        return {}
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        created = _parse_time(checkpoint.get("created_at"))
        if created and datetime.now() - created > timedelta(hours=max_age_hours):
            return {}
        if checkpoint.get("config_path") != str(config_path.resolve()):
            return {}
        if int(checkpoint.get("lookback_hours") or 0) != int(lookback_hours):
            return {}
        return checkpoint
    except Exception:
        return {}


def _save_checkpoint(checkpoint_path: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint["updated_at"] = now_iso()
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_checkpoint(checkpoint_path: Path) -> None:
    try:
        checkpoint_path.unlink()
    except FileNotFoundError:
        pass


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("id") or source.get("name", "unknown"))


def _cache_records(source_id: str, error: str | None = None) -> list[dict[str, Any]]:
    cached = _read_cache(source_id)
    for row in cached:
        payload = row.setdefault("payload", {})
        payload["from_cache"] = True
        if error:
            payload["cache_reason"] = error
    return cached


def _status(
    source: dict[str, Any],
    *,
    kind: str,
    provider_ok: bool,
    record_count: int,
    cache_used: bool,
    error: str | None,
    started_at: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "source_id": _source_id(source),
        "source_name": source.get("name", _source_id(source)),
        "kind": kind,
        "category": source.get("category", ""),
        "provider_ok": provider_ok,
        "record_count": record_count,
        "cache_used": cache_used,
        "error": error,
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def _record(
    source: dict[str, Any],
    title: str,
    summary: str,
    link: str,
    published_at: Any,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_id = _source_id(source)
    published = _parse_time(published_at)
    published_text = published.isoformat(timespec="seconds") if published else ""
    record = {
        "symbol": "GLOBAL",
        "section": "news",
        "source": str(source_id),
        "source_url": link,
        "published_at": published_text,
        "fetched_at": now_iso(),
        "record_key": make_record_key("GLOBAL", source_id, published_text, title),
        "payload": {
            "title": title or "",
            "summary": summary or "",
            "link": link or "",
            "source_name": source.get("name", source_id),
            "category": source.get("category", ""),
        },
    }
    if extra_payload:
        record["payload"].update(extra_payload)
    return record


def _url_matches_domain(url: str, expected_domain: str) -> bool:
    if not url or not expected_domain:
        return True
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme and parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    expected = expected_domain.lower().strip()
    return host == expected or host.endswith("." + expected)


def _newsnow_url(source: dict[str, Any]) -> str:
    if source.get("url"):
        return str(source["url"])
    platform_id = str(source.get("newsnow_id") or _source_id(source).removeprefix("newsnow_"))
    api_base = str(source.get("api_base") or NEWSNOW_DEFAULT_API_URL).rstrip("?")
    return f"{api_base}?id={platform_id}&latest"


def fetch_rss_source(
    source: dict[str, Any],
    *,
    lookback_hours: int = 24,
    timeout: int = 8,
    retries: int = 1,
) -> list[dict[str, Any]]:
    records, _ = fetch_rss_source_with_status(source, lookback_hours=lookback_hours, timeout=timeout, retries=retries)
    return records


def fetch_rss_source_with_status(
    source: dict[str, Any],
    *,
    lookback_hours: int = 24,
    timeout: int = 5,
    retries: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_id = _source_id(source)
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    started_at = now_iso()
    started = time.monotonic()
    try:
        import feedparser

        response = http_get(source["url"], timeout=timeout, retries=retries)
        feed = feedparser.parse(response.content)
        records: list[dict[str, Any]] = []
        limit = min(max(int(source.get("limit") or 80), 1), 300)
        for entry in feed.entries[:limit]:
            published_at = getattr(entry, "published", None) or getattr(entry, "updated", None)
            parsed = _parse_time(published_at)
            if parsed and parsed < cutoff:
                continue
            records.append(
                _record(
                    source,
                    entry.get("title", ""),
                    entry.get("summary", "") or entry.get("description", ""),
                    entry.get("link", ""),
                    published_at,
                )
            )
        if records:
            _write_cache(str(source_id), records)
        return records, _status(
            source,
            kind="rss",
            provider_ok=True,
            record_count=len(records),
            cache_used=False,
            error=None,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )
    except ModuleNotFoundError:
        error = "dependency_missing: feedparser"
        cached = _cache_records(str(source_id), error)
        return cached, _status(
            source,
            kind="rss",
            provider_ok=False,
            record_count=len(cached),
            cache_used=bool(cached),
            error=error,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
        cached = _cache_records(str(source_id), error)
        return cached, _status(
            source,
            kind="rss",
            provider_ok=False,
            record_count=len(cached),
            cache_used=bool(cached),
            error=error,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )


def _get_nested(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part, [])
        else:
            return []
    return current if isinstance(current, list) else []


def fetch_newsnow_hotlist_source_with_status(
    source: dict[str, Any],
    *,
    lookback_hours: int = 24,
    timeout: int = 5,
    retries: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_id = _source_id(source)
    started_at = now_iso()
    started = time.monotonic()
    try:
        response = http_get(_newsnow_url(source), timeout=timeout, retries=retries)
        data = response.json()
        status = str(data.get("status") or "")
        if status and status not in {"success", "cache"}:
            raise ValueError(f"unexpected NewsNow status: {status}")
        rows = data.get("items", [])
        if not isinstance(rows, list):
            raise ValueError("NewsNow response items is not a list")

        records: list[dict[str, Any]] = []
        limit = min(max(int(source.get("limit") or 50), 1), 300)
        platform_id = str(source.get("newsnow_id") or source_id.removeprefix("newsnow_"))
        expected_domain = str(source.get("expected_domain") or "")
        skipped_domain = 0
        for rank, row in enumerate(rows[:limit], 1):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            link = str(row.get("url") or row.get("mobileUrl") or "")
            mobile_url = str(row.get("mobileUrl") or "")
            if expected_domain and link and not _url_matches_domain(link, expected_domain):
                skipped_domain += 1
                continue
            published_at = row.get("pubDate") or row.get("published_at") or row.get("time") or row.get("date")
            summary = str(row.get("desc") or row.get("summary") or "")
            records.append(
                _record(
                    source,
                    title,
                    summary,
                    link,
                    published_at,
                    {
                        "source_type": "newsnow_hotlist",
                        "newsnow_platform_id": platform_id,
                        "newsnow_status": status,
                        "rank": rank,
                        "mobile_url": mobile_url,
                        "hot": row.get("hot", ""),
                        "raw_id": row.get("id", ""),
                    },
                )
            )
        if records:
            _write_cache(str(source_id), records)
        error = None if records or not skipped_domain else f"all_records_failed_domain_check: {skipped_domain}"
        return records, _status(
            source,
            kind="newsnow_hotlist",
            provider_ok=error is None,
            record_count=len(records),
            cache_used=False,
            error=error,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
        cached = _cache_records(str(source_id), error)
        return cached, _status(
            source,
            kind="newsnow_hotlist",
            provider_ok=False,
            record_count=len(cached),
            cache_used=bool(cached),
            error=error,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )


def fetch_api_source(
    source: dict[str, Any],
    *,
    lookback_hours: int = 24,
    timeout: int = 8,
    retries: int = 1,
) -> list[dict[str, Any]]:
    records, _ = fetch_api_source_with_status(source, lookback_hours=lookback_hours, timeout=timeout, retries=retries)
    return records


def fetch_api_source_with_status(
    source: dict[str, Any],
    *,
    lookback_hours: int = 24,
    timeout: int = 5,
    retries: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if source.get("type") == "newsnow_hotlist":
        return fetch_newsnow_hotlist_source_with_status(
            source,
            lookback_hours=lookback_hours,
            timeout=timeout,
            retries=retries,
        )
    source_id = _source_id(source)
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    started_at = now_iso()
    started = time.monotonic()
    try:
        response = http_get(source["url"], timeout=timeout, retries=retries)
        data = response.json()
        rows = _get_nested(data, source.get("data_path", ""))
        fields = source.get("fields", {})
        records: list[dict[str, Any]] = []
        limit = min(max(int(source.get("limit") or 80), 1), 300)
        for row in rows[:limit]:
            published_at = row.get(fields.get("time", "time"))
            parsed = _parse_time(published_at)
            if parsed and parsed < cutoff:
                continue
            records.append(
                _record(
                    source,
                    str(row.get(fields.get("title", "title"), "")),
                    str(row.get(fields.get("summary", "summary"), "")),
                    str(row.get(fields.get("link", "url"), "")),
                    published_at,
                )
            )
        if records:
            _write_cache(str(source_id), records)
        return records, _status(
            source,
            kind="api",
            provider_ok=True,
            record_count=len(records),
            cache_used=False,
            error=None,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
        cached = _cache_records(str(source_id), error)
        return cached, _status(
            source,
            kind="api",
            provider_ok=False,
            record_count=len(cached),
            cache_used=bool(cached),
            error=error,
            started_at=started_at,
            elapsed_seconds=time.monotonic() - started,
        )


def fetch_all_news(
    *,
    config_path: Path = DEFAULT_NEWS_SOURCES_PATH,
    lookback_hours: int = 24,
    categories: set[str] | None = None,
    max_workers: int = 8,
    timeout: int = 5,
    retries: int = 2,
    deadline_seconds: int | None = None,
    resume: bool = True,
    reset_checkpoint: bool = False,
    checkpoint_path: Path = CHECKPOINT_PATH,
    resume_max_age_hours: int = 6,
) -> list[dict[str, Any]]:
    records, _ = fetch_all_news_with_report(
        config_path=config_path,
        lookback_hours=lookback_hours,
        categories=categories,
        max_workers=max_workers,
        timeout=timeout,
        retries=retries,
        deadline_seconds=deadline_seconds,
        resume=resume,
        reset_checkpoint=reset_checkpoint,
        checkpoint_path=checkpoint_path,
        resume_max_age_hours=resume_max_age_hours,
    )
    return records


def fetch_all_news_with_report(
    *,
    config_path: Path = DEFAULT_NEWS_SOURCES_PATH,
    lookback_hours: int = 24,
    categories: set[str] | None = None,
    max_workers: int = 8,
    timeout: int = 5,
    retries: int = 2,
    deadline_seconds: int | None = None,
    resume: bool = True,
    reset_checkpoint: bool = False,
    checkpoint_path: Path = CHECKPOINT_PATH,
    resume_max_age_hours: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config_path = config_path.resolve()
    config = load_yaml(config_path)
    records: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    tasks: list[tuple[str, dict[str, Any]]] = []
    for source in config.get("rss_sources", []):
        if categories and source.get("category") not in categories:
            continue
        tasks.append(("rss", source))
    for source in config.get("api_sources", []):
        if categories and source.get("category") not in categories:
            continue
        tasks.append(("api", source))

    if reset_checkpoint:
        _clear_checkpoint(checkpoint_path)
    checkpoint = (
        _load_checkpoint(
            checkpoint_path,
            config_path=config_path,
            lookback_hours=lookback_hours,
            max_age_hours=resume_max_age_hours,
        )
        if resume
        else {}
    )
    if not checkpoint:
        checkpoint = {
            "version": 1,
            "source": SOURCE,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "config_path": str(config_path),
            "lookback_hours": lookback_hours,
            "completed": {},
        }
    completed = checkpoint.setdefault("completed", {})
    pending_tasks: list[tuple[str, dict[str, Any]]] = []
    for kind, source in tasks:
        source_id = _source_id(source)
        saved = completed.get(source_id) if resume else None
        if saved and saved.get("provider_ok"):
            cached = _read_cache(source_id)
            records.extend(cached)
            status = dict(saved)
            status["resumed"] = True
            status["cache_used"] = bool(cached)
            statuses.append(status)
        else:
            pending_tasks.append((kind, source))

    def run_task(kind: str, source: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if kind == "rss":
            return fetch_rss_source_with_status(source, lookback_hours=lookback_hours, timeout=timeout, retries=retries)
        return fetch_api_source_with_status(source, lookback_hours=lookback_hours, timeout=timeout, retries=retries)

    pool = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {}
    processed: set[Any] = set()
    try:
        future_map = {pool.submit(run_task, kind, source): (kind, source) for kind, source in pending_tasks}
        try:
            iterator = as_completed(future_map, timeout=deadline_seconds) if deadline_seconds else as_completed(future_map)
            for future in iterator:
                processed.add(future)
                kind, source = future_map[future]
                try:
                    source_records, status = future.result()
                    records.extend(source_records)
                    statuses.append(status)
                    if status.get("provider_ok"):
                        completed[_source_id(source)] = status
                        _save_checkpoint(checkpoint_path, checkpoint)
                except Exception as exc:
                    source_id = _source_id(source)
                    error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    cached = _cache_records(source_id, error)
                    records.extend(cached)
                    statuses.append(
                        _status(
                            source,
                            kind=kind,
                            provider_ok=False,
                            record_count=len(cached),
                            cache_used=bool(cached),
                            error=error,
                            started_at=now_iso(),
                            elapsed_seconds=0,
                        )
                    )
        except TimeoutError:
            pass

        for future, (kind, source) in future_map.items():
            if future in processed:
                continue
            if future.done():
                try:
                    source_records, status = future.result()
                    records.extend(source_records)
                    statuses.append(status)
                    if status.get("provider_ok"):
                        completed[_source_id(source)] = status
                        _save_checkpoint(checkpoint_path, checkpoint)
                except Exception as exc:
                    source_id = _source_id(source)
                    error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    cached = _cache_records(source_id, error)
                    records.extend(cached)
                    statuses.append(
                        _status(
                            source,
                            kind=kind,
                            provider_ok=False,
                            record_count=len(cached),
                            cache_used=bool(cached),
                            error=error,
                            started_at=now_iso(),
                            elapsed_seconds=0,
                        )
                    )
                continue
            future.cancel()
            source_id = _source_id(source)
            error = f"deadline_exceeded: news pool exceeded {deadline_seconds}s"
            cached = _cache_records(source_id, error)
            records.extend(cached)
            statuses.append(
                _status(
                    source,
                    kind=kind,
                    provider_ok=False,
                    record_count=len(cached),
                    cache_used=bool(cached),
                    error=error,
                    started_at=now_iso(),
                    elapsed_seconds=float(deadline_seconds),
                )
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = str(record.get("record_key") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(record)

    provider_failures = [item for item in statuses if not item["provider_ok"]]
    all_completed = len(completed) >= len(tasks)
    if all_completed:
        _clear_checkpoint(checkpoint_path)
    elif resume:
        _save_checkpoint(checkpoint_path, checkpoint)
    report = {
        "source": SOURCE,
        "total_sources": len(tasks),
        "completed_sources": len(statuses),
        "failed_sources": len(provider_failures),
        "deadline_seconds": deadline_seconds,
        "timeout_seconds": timeout,
        "retries": retries,
        "resume": resume,
        "checkpoint_path": str(checkpoint_path),
        "resumed_sources": sum(1 for item in statuses if item.get("resumed")),
        "checkpoint_completed_sources": len(completed),
        "checkpoint_cleared": all_completed,
        "record_count": len(deduped),
        "sources": sorted(statuses, key=lambda item: item["source_id"]),
    }
    return deduped, report
