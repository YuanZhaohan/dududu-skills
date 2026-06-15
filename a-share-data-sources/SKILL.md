---
name: a-share-data-sources
description: Production A-share financial data source layer for market quotes, financial statements, announcements, financial news, and forum sentiment. Use when Codex needs to fetch, refresh, normalize, store, or debug free A-share data sources, including Tencent quote APIs, CNINFO, Eastmoney, AkShare, BaoStock, RSS/API news feeds, and local incremental data storage.
---

# A-Share Data Sources

Use this skill as the production data layer for A-share research data. It is not a trading strategy and must not invent missing data.

## Workflow

1. Identify the section: `market`, `financial`, `announcements`, or `news`.
2. Use the section runner in `scripts/` for production refreshes.
3. Maintain individual providers in `scripts/sources/`.
4. Persist every run under `data/` with source, timestamp, record key, and error metadata.
5. Prefer free no-key sources. Keep token or paid sources out of the default path.

## Production Entrypoints

- `scripts/market_data.py`: Tencent realtime quote and daily momentum, with compressed market storage.
- `scripts/financial_data.py`: optional AkShare/BaoStock financial snapshots.
- `scripts/announcements.py`: CNINFO direct announcement fetch.
- `scripts/news_data.py`: shared RSS/API news pool plus Eastmoney Guba posts, with timeout retry and checkpoint resume.
- `scripts/update_all.py`: batch runner for symbol lists.
- `scripts/smoke_test.py`: concise health check for all data source sections.
- `scripts/repair_outputs.py`: maintenance helper to clean existing JSON/JSONL outputs.

The scripts expose Python functions first. Minimal `__main__` blocks exist only for smoke testing; formal CLI wrapping can be added later.

## News Resume

`scripts/news_data.py` writes successful news-source progress to `data/state/news_pool_checkpoint.json`.
If OpenClaw or another runner disconnects mid-run, rerun the same command and completed sources are skipped while failed or timed-out sources retry.
The checkpoint is cleared automatically only after all configured news sources complete successfully.
The current news pool has 79 configured news sources: 76 RSS feeds plus 3 JSON API feeds. Eastmoney Guba and Jiuyangongshe are separate per-symbol community sources, so the news/sentiment layer has 81 sources total.
Provider availability is network-dependent. The runner is considered healthy if it records failures and finishes; individual provider success should be checked from the run report or `data/state/catalog.sqlite`.
Read `references/forum-sentiment-sources.md` before adding cookie, browser, or Selenium-based community crawlers.

Useful commands:

- Resume/default run: `D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py 600519`
- Strict source order: `D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --max-workers 1 600519`
- Fresh rebuild: `D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --reset-checkpoint 600519`
- Short external-runner budget: `D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --timeout 5 --retries 2 --deadline 30 600519`

## Data Layout

- Raw responses: `data/raw/{section}/{source}/{symbol-or-global}/{timestamp}.json.gz`
- Market normalized data: `data/normalized/market/{symbol}.parquet`, falling back to `.jsonl.gz`
- Text/event normalized data: `data/normalized/{section}/{symbol}.jsonl`
- Financial normalized data: `data/normalized/financial/{symbol}.jsonl.gz`
- Run state and index: `data/state/catalog.sqlite`

## References

- Read `references/source-map.md` before changing provider priority.
- Read `references/provider-patterns.md` before changing fallback, retry, or cache behavior.
- Read `references/data-contract.md` before changing output schemas or storage paths.
- Read `references/imported-sources.md` for how the bundled reference projects informed this implementation.
- Bundled source-code references live under `references/UZI-Skill/`, `references/TradingAgents/`, and `references/ai-hedge-fund/`.
- Treat nested `SKILL.md` files inside reference projects as source material only; this folder's active entrypoint is `a-share-data-sources/SKILL.md`.

## Rules

- Do not silently swallow provider failures. Record attempts and errors.
- Do not overwrite valid data with weaker fallback data.
- Clean non-standard JSON values such as `NaN` or `Infinity` to `null` before writing.
- Do not delete `data/` or cache files to fix incremental issues unless the user explicitly asks for a rebuild.
- Do not copy API keys, SMTP passwords, or local private config values into this skill.
