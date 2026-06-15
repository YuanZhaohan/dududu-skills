from __future__ import annotations

import sys
from pathlib import Path

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
from sources.akshare_financial import SOURCE, fetch_financial_abstract  # noqa: E402


def update_financial(symbols: list[str] | None = None) -> list[FetchResult]:
    symbols = symbols or load_symbols()
    results: list[FetchResult] = []
    for symbol in symbols:
        plain = normalize_symbol(symbol)["plain"]
        started = now_iso()
        try:
            records = fetch_financial_abstract(plain)
            raw_path = write_raw_payload("financial", SOURCE, plain, {"records": records})
            normalized_path, _ = append_jsonl_records(NORMALIZED_DIR / "financial" / f"{plain}.jsonl.gz", records)
            ok = len(records) > 0
            result = FetchResult(
                "financial",
                SOURCE,
                plain,
                ok,
                records,
                raw_path=str(raw_path),
                normalized_path=str(normalized_path),
                error=None if ok else "akshare returned no financial records",
                attempts=[
                    SourceAttempt(
                        "financial",
                        SOURCE,
                        plain,
                        ok,
                        len(records),
                        None if ok else "akshare returned no financial records",
                        started,
                        now_iso(),
                    )
                ],
            )
        except Exception as exc:
            result = FetchResult(
                "financial",
                SOURCE,
                plain,
                False,
                [],
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
                attempts=[SourceAttempt("financial", SOURCE, plain, False, 0, str(exc)[:200], started, now_iso())],
            )
        catalog_attempt(result)
        results.append(result)
    return results


if __name__ == "__main__":
    symbols_arg = sys.argv[1:] or None
    emit_json([result_to_dict(r) for r in update_financial(symbols_arg)])
