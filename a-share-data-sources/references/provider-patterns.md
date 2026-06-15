# Provider Patterns

## Fallback

- Keep fallback chains per section. Do not use one global provider order.
- Trigger fallback only on timeout, empty data, structural parse failure, or missing required fields.
- Record every attempt in `data/state/catalog.sqlite`.
- Do not overwrite stronger existing records with weaker fallback records.

## Incremental Updates

- Every normalized record needs a deterministic `record_key`.
- Re-running the same refresh must not duplicate records.
- Do not delete cache or normalized outputs to fix refresh problems. Rebuild only when explicitly requested.
- For batch jobs, continue other symbols when one symbol fails.

## Storage

- Raw responses are immutable `.json.gz` files.
- SQLite stores run metadata, status, record keys, paths, and errors. It does not store large text bodies.
- Market tables are compacted by key and saved as Parquet when possible.
- News, announcements, and forum posts stay JSONL for inspection.

## Imported Production Lessons

- `references/UZI-Skill/`: A-share source priority, CNINFO slow-path avoidance, BaoStock fallback for Windows/TLS issues.
- `references/TradingAgents/`: vendor routing and no-data semantics to prevent fabricated data.
- `references/ai-hedge-fund/`: typed records, cache boundaries, and 429 retry/backoff.
- `金融数据源_v2`: shared RSS/API source pool, direct-first/cache-fallback news fetching, Tencent quote batching, Eastmoney Guba sentiment.
