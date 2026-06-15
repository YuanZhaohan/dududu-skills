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
from sources.cninfo import SOURCE, fetch_announcements  # noqa: E402


def update_announcements(symbols: list[str] | None = None, *, page_size: int = 30) -> list[FetchResult]:
    symbols = symbols or load_symbols()
    results: list[FetchResult] = []
    for symbol in symbols:
        plain = normalize_symbol(symbol)["plain"]
        started = now_iso()
        try:
            records, payload = fetch_announcements(plain, page_size=page_size)
            raw_path = write_raw_payload("announcements", SOURCE, plain, payload)
            normalized_path, _ = append_jsonl_records(NORMALIZED_DIR / "announcements" / f"{plain}.jsonl", records)
            ok = len(records) > 0
            result = FetchResult(
                "announcements",
                SOURCE,
                plain,
                ok,
                records,
                raw_path=str(raw_path),
                normalized_path=str(normalized_path),
                error=None if ok else "cninfo returned no records",
                attempts=[
                    SourceAttempt(
                        "announcements",
                        SOURCE,
                        plain,
                        ok,
                        len(records),
                        None if ok else "cninfo returned no records",
                        started,
                        now_iso(),
                    )
                ],
            )
        except Exception as exc:
            result = FetchResult(
                "announcements",
                SOURCE,
                plain,
                False,
                [],
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
                attempts=[SourceAttempt("announcements", SOURCE, plain, False, 0, str(exc)[:200], started, now_iso())],
            )
        catalog_attempt(result)
        results.append(result)
    return results


if __name__ == "__main__":
    symbols_arg = sys.argv[1:] or None
    emit_json([result_to_dict(r) for r in update_announcements(symbols_arg)])
