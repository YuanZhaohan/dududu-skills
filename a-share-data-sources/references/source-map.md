# Source Map

## Market

Primary production path:

1. `tencent_quote`: realtime quote, market value, PE, turnover, daily K-line momentum.
2. `akshare`: optional deeper historical K-line if installed.
3. `efinance`: optional redundant Eastmoney-style quote/K-line source.
4. `baostock`: optional fallback for A-share historical data when HTTPS sources fail.

Store market data compressed. Prefer Parquet with zstd. Fall back to `.jsonl.gz` when `pyarrow` is unavailable.

## Financial

Primary production path:

1. `akshare`: financial abstract and indicator tables.
2. `baostock`: fallback quarterly profit data.
3. `eastmoney_direct`: future extension for direct HTTP financial tables.

Financial fields drift across sources. Store normalized records as `.jsonl.gz`.

## Announcements

Primary production path:

1. `cninfo_direct`: direct `/new/hisAnnouncement/query` first pages with bounded page size.
2. `eastmoney`: future extension for announcement redundancy.
3. `akshare_limited`: only opt-in for known bounded functions.

Avoid AkShare CNINFO functions that fetch all pages before returning.

## News And Sentiment

Primary production path:

1. `news_pool`: sanitized RSS/API pool imported from `金融数据源_v2`.
2. `eastmoney_guba`: stock forum post count, clicks, and latest post titles.
3. `jiuyangongshe`: stock/community article mentions from public search pages.
4. UZI-style direct news providers: Jin10, Eastmoney flash, Eastmoney announcements, THS news.
5. Optional cookie/browser sources: Xueqiu, Taoguba, Jisilu. Keep these out of the default path unless explicitly enabled.
6. Optional DDGS search for sparse symbols only.

Text records stay as JSONL to preserve titles, summaries, links, and original source fields.
Read `forum-sentiment-sources.md` before adding cookie, browser, or Selenium-based community crawlers.
