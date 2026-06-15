# Imported Sources

This skill is the production implementation. The folders below are reference code only; do not run their setup scripts or copy private configuration into `a-share-data-sources`.

## `金融数据源_v2`

Original local reference path: `C:\Users\dududu\Desktop\金融数据源_v2`.

Use as the fourth reference project.

- `sources.yaml`: sanitized into `data/input/news_sources.yaml`; keep public RSS/API source definitions only.
- `track-pulse/fetcher.py`: direct-first news fetching, retry, timeout, and local cache fallback.
- `industry-sentiment-tracker/scripts/industry_stock_pipeline.py`: Tencent quote batching, 5-day momentum, Eastmoney Guba post extraction, and industry-level aggregation.
- `industry-sentiment-tracker/scripts/industry_sentiment_report.py`: sentiment reporting is useful downstream, but LLM calls are not part of this data-source layer.

Do not copy API keys, SMTP credentials, or local user config values from this project.

## `references/UZI-Skill/`

Use for A-share source priority and known data-source pitfalls:

- AkShare is broad but unstable.
- BaoStock is useful fallback when HTTPS sources fail.
- CNINFO direct first-page API is safer than AkShare CNINFO full-pagination wrappers.
- News providers such as Jin10, Eastmoney, THS, Xueqiu, and forum/browser fallbacks are useful source-pattern references.

Useful paths:

- `references/UZI-Skill/docs/DATA-PROVIDERS.md`
- `references/UZI-Skill/commands/analyze-stock.md`
- `references/UZI-Skill/skills/deep-analysis/scripts/lib/`

## `references/TradingAgents/`

Use for routing behavior:

- Per-section vendor maps.
- Explicit no-data states.
- Continue fallbacks on rate-limit or provider-specific failures.

Useful paths:

- `references/TradingAgents/tradingagents/dataflows/`
- `references/TradingAgents/tradingagents/agents/analysts/`
- `references/TradingAgents/tradingagents/graph/`

## `references/ai-hedge-fund/`

Use for production hygiene:

- Typed data records.
- Cache boundaries by endpoint and date.
- Retry/backoff on rate limits.

Useful paths:

- `references/ai-hedge-fund/src/`
- `references/ai-hedge-fund/app/backend/`