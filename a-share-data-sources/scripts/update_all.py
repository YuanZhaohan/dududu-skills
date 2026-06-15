from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from announcements import update_announcements  # noqa: E402
from common import emit_json, load_symbols, result_to_dict  # noqa: E402
from financial_data import update_financial  # noqa: E402
from market_data import update_market  # noqa: E402
from news_data import update_news  # noqa: E402


def update_all(symbols: list[str] | None = None) -> dict[str, object]:
    symbols = symbols or load_symbols()
    market = update_market(symbols)
    financial = update_financial(symbols)
    announcements = update_announcements(symbols)
    news = update_news(symbols)
    return {
        "symbols": symbols,
        "market": result_to_dict(market),
        "financial": [result_to_dict(r) for r in financial],
        "announcements": [result_to_dict(r) for r in announcements],
        "news": [result_to_dict(r) for r in news],
    }


if __name__ == "__main__":
    symbols_arg = sys.argv[1:] or None
    emit_json(update_all(symbols_arg))
