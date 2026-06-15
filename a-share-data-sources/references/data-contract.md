# Data Contract

## Common Fields

Every normalized record must include:

- `symbol`: normalized plain A-share code when symbol-specific, or `GLOBAL`.
- `section`: `market`, `financial`, `announcements`, `news`, or `forum`.
- `source`: provider id.
- `source_api` or `source_url`.
- `record_key`: deterministic de-duplication key.
- `fetched_at`: local ISO timestamp.
- `payload`: source-specific fields.

## Record Keys

- Market: `symbol + trade_date + source + adjust`
- Financial: `symbol + report_date + metric + source`
- Announcements: `symbol + announcement_id/url/title_hash`
- News: `GLOBAL/symbol + source + published_at + title_hash`
- Forum: `symbol + source + published_at + title_hash`

## Paths

- Raw response: `data/raw/{section}/{source}/{symbol-or-global}/{timestamp}.json.gz`
- Market: `data/normalized/market/{symbol}.parquet` or `{symbol}.jsonl.gz`
- Financial: `data/normalized/financial/{symbol}.jsonl.gz`
- Announcements: `data/normalized/announcements/{symbol}.jsonl`
- News: `data/normalized/news/{symbol-or-GLOBAL}.jsonl`
- Forum: `data/normalized/forum/{symbol}.jsonl`
- Catalog: `data/state/catalog.sqlite`
