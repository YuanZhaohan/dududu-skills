from __future__ import annotations

import gzip
import hashlib
import json
import math
import numbers
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
STATE_DIR = DATA_DIR / "state"
CATALOG_PATH = STATE_DIR / "catalog.sqlite"
DEFAULT_SYMBOLS_PATH = INPUT_DIR / "symbols.txt"
DEFAULT_NEWS_SOURCES_PATH = INPUT_DIR / "news_sources.yaml"


@dataclass
class SourceAttempt:
    section: str
    source: str
    symbol: str
    ok: bool
    record_count: int = 0
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""


@dataclass
class FetchResult:
    section: str
    source: str
    symbol: str
    ok: bool
    records: list[dict[str, Any]]
    raw_path: str | None = None
    normalized_path: str | None = None
    error: str | None = None
    attempts: list[SourceAttempt] | None = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_data_dirs() -> None:
    for path in (INPUT_DIR, RAW_DIR, NORMALIZED_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def normalize_symbol(symbol: str) -> dict[str, str]:
    raw = str(symbol).strip().upper()
    raw = raw.replace(".", "").replace("_", "").replace("-", "")
    raw = raw.removeprefix("SH").removeprefix("SZ").removeprefix("BJ")
    if not re.fullmatch(r"\d{6}", raw):
        raise ValueError(f"Invalid A-share symbol: {symbol!r}")
    if raw.startswith(("6", "9")):
        exchange = "sh"
    elif raw.startswith(("8", "4")):
        exchange = "bj"
    else:
        exchange = "sz"
    return {
        "plain": raw,
        "exchange": exchange,
        "tencent": f"{exchange}{raw}",
        "cninfo": raw,
        "baostock": f"{exchange}.{raw}",
    }


def load_symbols(path: Path = DEFAULT_SYMBOLS_PATH) -> list[str]:
    if not path.exists():
        return ["600519", "000001"]
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        symbols.append(normalize_symbol(item)["plain"])
    return symbols


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def make_record_key(*parts: Any) -> str:
    text = "|".join("" if p is None else str(p) for p in parts)
    return stable_hash(text, 24)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def clean_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        item = float(value)
        if math.isnan(item) or math.isinf(item):
            return None
        return item if type(value).__module__.startswith("numpy") else value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): clean_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_json_value(v) for v in value]
    if hasattr(value, "item"):
        try:
            return clean_json_value(value.item())
        except Exception:
            pass
    return value


def write_json_gz(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(clean_json_value(payload), f, ensure_ascii=False, default=json_default, allow_nan=False)
    return path


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_raw_payload(section: str, source: str, symbol: str, payload: Any) -> Path:
    ensure_data_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / safe_name(section) / safe_name(source) / safe_name(symbol) / f"{stamp}.json.gz"
    return write_json_gz(path, payload)


def _open_text(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def append_jsonl_records(path: Path, records: Iterable[dict[str, Any]], key_field: str = "record_key") -> tuple[Path, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    existing_records: list[dict[str, Any]] = []
    needs_rewrite = False
    if path.exists():
        with _open_text(path, "rt") as f:
            for line in f:
                if not line.strip():
                    continue
                if any(marker in line for marker in ("NaN", "Infinity", "-Infinity")):
                    needs_rewrite = True
                try:
                    row = json.loads(line)
                    key = row.get(key_field)
                    if key:
                        if str(key) in seen:
                            needs_rewrite = True
                            continue
                        seen.add(str(key))
                    existing_records.append(row)
                except json.JSONDecodeError:
                    needs_rewrite = True
                    continue

    written = 0
    mode = "wt" if needs_rewrite else "at"
    with _open_text(path, mode) as f:
        if needs_rewrite:
            for record in existing_records:
                f.write(json.dumps(clean_json_value(record), ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
        for record in records:
            key = record.get(key_field)
            if key and str(key) in seen:
                continue
            f.write(json.dumps(clean_json_value(record), ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
            if key:
                seen.add(str(key))
            written += 1
    return path, written


def write_market_records(symbol: str, records: list[dict[str, Any]], key_fields: list[str]) -> tuple[Path, int]:
    market_dir = NORMALIZED_DIR / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = market_dir / f"{normalize_symbol(symbol)['plain']}.parquet"
    fallback_path = market_dir / f"{normalize_symbol(symbol)['plain']}.jsonl.gz"
    if not records:
        return parquet_path if parquet_path.exists() else fallback_path, 0

    try:
        import pandas as pd

        new_df = pd.DataFrame(records)
        if parquet_path.exists():
            old_df = pd.read_parquet(parquet_path)
            merged = pd.concat([old_df, new_df], ignore_index=True)
        else:
            merged = new_df
        subset = [col for col in key_fields if col in merged.columns]
        if subset:
            before = len(merged)
            merged = merged.drop_duplicates(subset=subset, keep="last")
            written = max(0, len(merged) - (before - len(new_df)))
        else:
            written = len(new_df)
        merged.to_parquet(parquet_path, index=False, compression="zstd")
        return parquet_path, written
    except Exception:
        return append_jsonl_records(fallback_path, records)


def init_catalog() -> None:
    ensure_data_dirs()
    with sqlite3.connect(CATALOG_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section TEXT NOT NULL,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                ok INTEGER NOT NULL,
                record_count INTEGER NOT NULL,
                error TEXT,
                raw_path TEXT,
                normalized_path TEXT,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                record_key TEXT PRIMARY KEY,
                section TEXT NOT NULL,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                output_path TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def catalog_attempt(result: FetchResult) -> None:
    init_catalog()
    attempts = result.attempts or [
        SourceAttempt(
            section=result.section,
            source=result.source,
            symbol=result.symbol,
            ok=result.ok,
            record_count=len(result.records),
            error=result.error,
            started_at=now_iso(),
            finished_at=now_iso(),
        )
    ]
    with sqlite3.connect(CATALOG_PATH) as conn:
        for attempt in attempts:
            conn.execute(
                """
                INSERT INTO attempts
                (section, source, symbol, ok, record_count, error, raw_path, normalized_path, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.section,
                    attempt.source,
                    attempt.symbol,
                    1 if attempt.ok else 0,
                    attempt.record_count,
                    attempt.error,
                    result.raw_path,
                    result.normalized_path,
                    attempt.started_at,
                    attempt.finished_at,
                ),
            )
        for record in result.records:
            key = record.get("record_key")
            if not key:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO records
                (record_key, section, source, symbol, output_path, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    record.get("section", result.section),
                    record.get("source", result.source),
                    record.get("symbol", result.symbol),
                    result.normalized_path,
                    record.get("fetched_at", now_iso()),
                ),
            )
        conn.commit()


def http_get(url: str, *, timeout: int = 15, retries: int = 3, headers: dict[str, str] | None = None, trust_env: bool = False):
    import requests

    session = requests.Session()
    session.trust_env = trust_env
    delay = 2.0
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout, headers=headers or {"User-Agent": "Mozilla/5.0"})
            if response.status_code in (429, 503) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            return response
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(last_error or f"GET failed: {url}")


def load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def result_to_dict(result: FetchResult) -> dict[str, Any]:
    data = asdict(result)
    data["record_count"] = len(result.records)
    return data


def emit_json(payload: Any) -> None:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(json.dumps(clean_json_value(payload), ensure_ascii=False, indent=2, default=json_default, allow_nan=False))
    sys.stdout.write("\n")
