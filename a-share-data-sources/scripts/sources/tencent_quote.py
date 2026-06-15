from __future__ import annotations

from typing import Any

from common import http_get, make_record_key, normalize_symbol, now_iso


SOURCE = "tencent_quote"


def parse_quote_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        exchange = name[2:4].lower() if name.startswith("v_") else ""
        fields = raw_value.strip().strip('"').strip("'").split("~")
        if len(fields) < 47:
            continue
        try:
            code = fields[2]
            fetched_at = now_iso()
            trade_date = fields[30] if len(fields) > 30 else fetched_at[:10]
            payload = {
                "name": fields[1],
                "price": float(fields[3]) if fields[3] else None,
                "prev_close": float(fields[4]) if fields[4] else None,
                "open": float(fields[5]) if fields[5] else None,
                "volume": float(fields[6]) if fields[6] else None,
                "high": float(fields[33]) if len(fields) > 33 and fields[33] else None,
                "low": float(fields[34]) if len(fields) > 34 and fields[34] else None,
                "change_pct": float(fields[32]) if len(fields) > 32 and fields[32] else None,
                "turnover": float(fields[38]) if len(fields) > 38 and fields[38] else None,
                "pe": float(fields[39]) if len(fields) > 39 and fields[39] else None,
                "total_mv_yi": float(fields[45]) if len(fields) > 45 and fields[45] else None,
            }
            records.append(
                {
                    "symbol": code,
                    "section": "market",
                    "source": SOURCE,
                    "source_api": "https://qt.gtimg.cn/q=",
                    "trade_date": trade_date,
                    "adjust": "none",
                    "fetched_at": fetched_at,
                    "record_key": make_record_key(code, trade_date, SOURCE, "quote"),
                    "payload": payload,
                    **payload,
                    "exchange": exchange,
                }
            )
        except (ValueError, IndexError):
            continue
    return records


def fetch_quotes(symbols: list[str], batch_size: int = 500) -> tuple[list[dict[str, Any]], list[str]]:
    tencent_codes = [normalize_symbol(symbol)["tencent"] for symbol in symbols]
    all_records: list[dict[str, Any]] = []
    raw_chunks: list[str] = []
    for start in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[start : start + batch_size]
        response = http_get(f"https://qt.gtimg.cn/q={','.join(batch)}", timeout=15, retries=3)
        text = response.text
        raw_chunks.append(text)
        all_records.extend(parse_quote_text(text))
    return all_records, raw_chunks


def fetch_daily_kline(symbol: str, days: int = 6) -> list[list[Any]]:
    code = normalize_symbol(symbol)["tencent"]
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    response = http_get(url, timeout=8, retries=3)
    payload = response.json()
    data = payload.get("data", {})
    item = data.get(code) or data.get(code.upper()) or {}
    return item.get("qfqday") or item.get("day") or []


def fetch_5d_momentum(symbol: str) -> dict[str, Any] | None:
    klines = fetch_daily_kline(symbol, days=6)
    if len(klines) < 2:
        return None
    start = float(klines[0][2])
    latest = float(klines[-1][2])
    plain = normalize_symbol(symbol)["plain"]
    trade_date = str(klines[-1][0])
    return {
        "symbol": plain,
        "section": "market",
        "source": SOURCE,
        "source_api": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        "trade_date": trade_date,
        "adjust": "qfq",
        "fetched_at": now_iso(),
        "record_key": make_record_key(plain, trade_date, SOURCE, "momentum_5d"),
        "payload": {"mom_5d": round((latest - start) / start * 100, 2), "kline_count": len(klines)},
        "mom_5d": round((latest - start) / start * 100, 2),
    }
