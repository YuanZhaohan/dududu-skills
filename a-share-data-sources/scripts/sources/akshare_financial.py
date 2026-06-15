from __future__ import annotations

from typing import Any

from common import make_record_key, normalize_symbol, now_iso


SOURCE = "akshare"


def fetch_financial_abstract(symbol: str) -> list[dict[str, Any]]:
    plain = normalize_symbol(symbol)["plain"]
    import akshare as ak  # type: ignore

    df = ak.stock_financial_abstract(symbol=plain)
    if df is None or df.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        report_date = str(row.get("报告期") or row.get("日期") or row.get("date") or "")
        metric = str(row.get("指标") or row.get("项目") or row.get("metric") or "financial_abstract")
        records.append(
            {
                "symbol": plain,
                "section": "financial",
                "source": SOURCE,
                "source_api": "akshare.stock_financial_abstract",
                "report_date": report_date,
                "metric": metric,
                "fetched_at": now_iso(),
                "record_key": make_record_key(plain, SOURCE, report_date, metric, row),
                "payload": row,
            }
        )
    return records
