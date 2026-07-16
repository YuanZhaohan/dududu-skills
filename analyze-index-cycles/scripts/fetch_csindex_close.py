"""Download daily index closes from the public CSI index-performance API."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


API_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
INDEX_PAGE = "https://www.csindex.com.cn/zh-CN/indices/index-detail/{index_code}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


def _compact_date(value: str) -> str:
    parsed = pd.to_datetime(value, errors="raise")
    return parsed.strftime("%Y%m%d")


def _request_json(url: str, *, timeout: int, retries: int) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.csindex.com.cn/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network failures need the original exception context
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"CSI public API failed after {retries} attempts: {last_error}") from last_error


def fetch_close_series(
    index_code: str,
    start_date: str,
    end_date: str,
    *,
    timeout: int = 60,
    retries: int = 3,
) -> tuple[pd.DataFrame, dict]:
    """Fetch, standardize, and validate one CSI daily index series."""

    normalized_code = str(index_code).strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{6}", normalized_code):
        raise ValueError("index_code must contain exactly six digits or uppercase letters")

    start = _compact_date(start_date)
    end = _compact_date(end_date)
    if start > end:
        raise ValueError("start_date must not be later than end_date")

    query = urlencode(
        {"indexCode": normalized_code, "startDate": start, "endDate": end}
    )
    source_url = f"{API_URL}?{query}"
    payload = _request_json(source_url, timeout=timeout, retries=retries)
    if str(payload.get("code")) != "200" or payload.get("success") is not True:
        raise RuntimeError(
            f"CSI public API returned an unsuccessful response: "
            f"code={payload.get('code')!r}, msg={payload.get('msg')!r}"
        )

    records = payload.get("data")
    if not isinstance(records, list) or not records:
        raise ValueError(f"CSI public API returned no rows for {normalized_code}")

    raw = pd.DataFrame.from_records(records)
    required = {"tradeDate", "indexCode", "indexNameCn", "close"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"CSI response is missing required fields: {missing}")

    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    frame = pd.DataFrame(
        {
            "日期": pd.to_datetime(raw["tradeDate"], format="%Y%m%d", errors="coerce"),
            "代码": raw["indexCode"].astype(str).str.zfill(6) + ".CSI",
            "指数名称": raw["indexNameCn"].astype("string"),
            "开盘价": pd.to_numeric(raw.get("open"), errors="coerce"),
            "最高价": pd.to_numeric(raw.get("high"), errors="coerce"),
            "最低价": pd.to_numeric(raw.get("low"), errors="coerce"),
            "收盘价": pd.to_numeric(raw["close"], errors="coerce"),
            "涨跌": pd.to_numeric(raw.get("change"), errors="coerce"),
            "涨跌幅": pd.to_numeric(raw.get("changePct"), errors="coerce"),
            "成交量": pd.to_numeric(raw.get("tradingVol"), errors="coerce"),
            "成交额": pd.to_numeric(raw.get("tradingValue"), errors="coerce"),
        }
    )
    frame["数据源"] = "中证指数有限公司（CSI）"
    frame["来源接口"] = source_url
    frame["抓取时间"] = fetched_at

    if frame["日期"].isna().any():
        raise ValueError("CSI response contains an unparseable tradeDate")
    if frame["日期"].duplicated().any():
        duplicated = frame.loc[frame["日期"].duplicated(keep=False), "日期"]
        examples = duplicated.dt.strftime("%Y-%m-%d").unique().tolist()[:5]
        raise ValueError(f"CSI response contains duplicate trade dates: {examples}")
    if frame["收盘价"].isna().any() or (frame["收盘价"] <= 0).any():
        raise ValueError("CSI response contains missing, non-numeric, or non-positive closes")
    if frame["代码"].nunique() != 1 or frame["指数名称"].nunique() != 1:
        raise ValueError("CSI response contains more than one index")

    frame = frame.sort_values("日期").reset_index(drop=True)
    frame["日期"] = frame["日期"].dt.strftime("%Y-%m-%d")
    metadata = {
        "index_code": normalized_code,
        "index_name": str(frame["指数名称"].iloc[0]),
        "source": "中证指数官网公开指数表现接口",
        "source_url": source_url,
        "index_page": INDEX_PAGE.format(index_code=normalized_code),
        "requested_start": start,
        "requested_end": end,
        "rows": int(len(frame)),
        "date_start": str(frame["日期"].iloc[0]),
        "date_end": str(frame["日期"].iloc[-1]),
        "latest_close": float(frame["收盘价"].iloc[-1]),
        "fetched_at": fetched_at,
        "notes": [
            "保留官网返回的实际交易日，不补周末或节假日。",
            "收盘价为指数点位，不是可直接交易工具的价格。",
        ],
    }
    return frame, metadata


def write_close_series(frame: pd.DataFrame, metadata: dict, output: str | Path) -> dict:
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.10g")
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        **metadata,
        "output": str(output_path),
        "metadata_output": str(metadata_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从中证指数官网公开接口下载指数日收盘价。"
    )
    parser.add_argument("--index-code", default="000985")
    parser.add_argument("--start-date", default="2004-12-31")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, default=Path("000985_daily.csv"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame, metadata = fetch_close_series(
        args.index_code,
        args.start_date,
        args.end_date,
        timeout=args.timeout,
        retries=args.retries,
    )
    result = write_close_series(frame, metadata, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
