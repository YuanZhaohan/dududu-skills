from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    FetchResult,
    SourceAttempt,
    catalog_attempt,
    emit_json,
    load_symbols,
    normalize_symbol,
    now_iso,
    result_to_dict,
    write_market_records,
    write_raw_payload,
)
from sources.tencent_quote import SOURCE, fetch_5d_momentum, fetch_quotes  # noqa: E402


def update_market(symbols: list[str] | None = None, *, include_momentum: bool = True) -> FetchResult:
    symbols = symbols or load_symbols()
    plain_symbols = [normalize_symbol(symbol)["plain"] for symbol in symbols]
    started = now_iso()
    attempts: list[SourceAttempt] = []
    try:
        quote_records, raw_chunks = fetch_quotes(plain_symbols)
        raw_path = write_raw_payload("market", SOURCE, "batch", {"symbols": plain_symbols, "raw": raw_chunks})
        records = quote_records
        quoted_symbols = {str(record.get("symbol")) for record in quote_records}
        for symbol in plain_symbols:
            if symbol not in quoted_symbols:
                attempts.append(
                    SourceAttempt(
                        "market",
                        SOURCE,
                        symbol,
                        False,
                        0,
                        "quote returned no record",
                        started,
                        now_iso(),
                    )
                )
        if include_momentum:
            for symbol in plain_symbols:
                try:
                    item = fetch_5d_momentum(symbol)
                    if item:
                        records.append(item)
                except Exception as exc:
                    attempts.append(
                        SourceAttempt(
                            "market",
                            SOURCE,
                            symbol,
                            False,
                            0,
                            f"momentum: {type(exc).__name__}: {str(exc)[:120]}",
                            started,
                            now_iso(),
                        )
                    )
        normalized_paths: list[str] = []
        written_total = 0
        for symbol in plain_symbols:
            rows = [r for r in records if r.get("symbol") == symbol]
            path, written = write_market_records(symbol, rows, ["symbol", "trade_date", "source", "adjust", "record_key"])
            normalized_paths.append(str(path))
            written_total += written
        ok = len(records) > 0
        result = FetchResult(
            section="market",
            source=SOURCE,
            symbol=",".join(plain_symbols),
            ok=ok,
            records=records,
            raw_path=str(raw_path),
            normalized_path=";".join(sorted(set(normalized_paths))),
            attempts=attempts
            + [
                SourceAttempt(
                    "market",
                    SOURCE,
                    ",".join(plain_symbols),
                    ok,
                    len(records),
                    None if ok else "tencent quote returned no records",
                    started,
                    now_iso(),
                )
            ],
        )
        catalog_attempt(result)
        return result
    except Exception as exc:
        result = FetchResult(
            section="market",
            source=SOURCE,
            symbol=",".join(plain_symbols),
            ok=False,
            records=[],
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            attempts=[SourceAttempt("market", SOURCE, ",".join(plain_symbols), False, 0, str(exc)[:200], started, now_iso())],
        )
        catalog_attempt(result)
        return result


if __name__ == "__main__":
    symbols_arg = sys.argv[1:] or None
    emit_json(result_to_dict(update_market(symbols_arg)))
