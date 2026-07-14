from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common import DEFAULT_SYMBOLS_PATH, clean_json_value, load_symbols, normalize_symbol, now_iso
from sources.eastmoney_guba import fetch_a_share_symbols, refresh_symbols_file

MIN_FULL_UNIVERSE_SYMBOLS = 1000


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_json_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    source = symbols if symbols else load_symbols()
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in source:
        plain = normalize_symbol(symbol)["plain"]
        if plain in seen:
            continue
        seen.add(plain)
        normalized.append(plain)
    return normalized


def _local_symbols() -> list[str]:
    return normalize_symbols(None)


def _local_refresh_payload(error: Exception, *, include_bj: bool, mode: str) -> dict[str, Any]:
    symbols = _local_symbols()
    return {
        "ok": False,
        "source": "local_symbols_txt",
        "fallback_to_local": True,
        "fallback_reason": f"{type(error).__name__}: {str(error)[:300]}",
        "symbol_count": len(symbols),
        "include_bj": include_bj,
        "written": False,
        "mode": mode,
        "generated_at": now_iso(),
    }


def should_refresh_symbols(explicit_symbols: list[str] | None, refresh_symbols: str) -> bool:
    if explicit_symbols:
        return False
    if refresh_symbols == "always":
        return True
    if refresh_symbols == "never":
        return False
    if not DEFAULT_SYMBOLS_PATH.exists():
        return True
    try:
        return len(load_symbols()) < MIN_FULL_UNIVERSE_SYMBOLS
    except Exception:
        return True


def resolve_symbols(
    *,
    explicit_symbols: list[str] | None,
    plan_only: bool,
    refresh_symbols: str,
    include_bj: bool,
    timeout: int,
    retries: int,
) -> tuple[list[str], dict[str, Any] | None]:
    if refresh_symbols not in {"auto", "always", "never"}:
        raise ValueError("refresh_symbols must be auto, always, or never")
    if explicit_symbols:
        return normalize_symbols(explicit_symbols), None
    if plan_only and refresh_symbols != "never":
        try:
            rows = fetch_a_share_symbols(include_bj=include_bj, timeout=timeout, retries=retries)
            source = "akshare_stock_info_a_code_name" if all(row.get("pool") == "akshare_a" for row in rows) else "eastmoney_clist_live_plan"
            return [row["symbol"] for row in rows], {
                "ok": True,
                "source": source,
                "symbol_count": len(rows),
                "include_bj": include_bj,
                "written": False,
                "generated_at": now_iso(),
            }
        except Exception as exc:
            local = _local_symbols()
            return local, _local_refresh_payload(exc, include_bj=include_bj, mode="plan")
    if should_refresh_symbols(explicit_symbols, refresh_symbols):
        try:
            refresh = refresh_symbols_file(include_bj=include_bj, timeout=timeout, retries=retries)
            refresh["ok"] = True
            return normalize_symbols(None), refresh
        except Exception as exc:
            local = _local_symbols()
            if len(local) >= MIN_FULL_UNIVERSE_SYMBOLS:
                return local, _local_refresh_payload(exc, include_bj=include_bj, mode="batch")
            raise RuntimeError(
                "Symbol refresh failed and local symbols.txt is not a full universe "
                f"({len(local)} symbols). Pass explicit symbols for probes or use --refresh-symbols never intentionally. "
                f"Original error: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
    return normalize_symbols(None), None


def slice_batch(symbols: list[str], batch_size: int, batch_index: int) -> list[str]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if batch_index < 0:
        raise ValueError("batch_index must be >= 0")
    start = batch_index * batch_size
    return symbols[start : start + batch_size]


def batch_count(symbols: list[str], batch_size: int) -> int:
    if not symbols:
        return 0
    return (len(symbols) + batch_size - 1) // batch_size


def build_batches(symbols: list[str], batch_size: int) -> list[dict[str, int]]:
    return [
        {"batch_index": index, "selected_symbol_count": len(slice_batch(symbols, batch_size, index))}
        for index in range(batch_count(symbols, batch_size))
    ]