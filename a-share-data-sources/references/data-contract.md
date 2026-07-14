# 数据契约

## 通用字段

每条标准化记录都必须包含：

- `symbol`：股票级数据使用 6 位 A 股代码；全局数据使用 `GLOBAL`。
- `section`：`market`、`financial`、`announcements`、`news`、`forum`。
- `source`：provider id。
- `source_api` 或 `source_url`：来源接口或页面。
- `record_key`：确定性去重 key。
- `fetched_at`：本地 ISO 时间戳。
- `payload`：来源特定字段。

## Record Key

- 行情：`symbol + trade_date + source + adjust`
- 财务：`symbol + report_date + metric + source`
- 公告：`symbol + announcement_id/url/title_hash`
- 新闻：`GLOBAL/symbol + source + published_at + title_hash`
- 股吧/社区：`symbol + source + published_at + title_hash`

## 路径

- 原始响应：`data/raw/{section}/{source}/{symbol-or-global}/{timestamp}.json.gz`
- 行情：`data/normalized/market/{symbol}.parquet` 或 `{symbol}.jsonl.gz`
- 财务：`data/normalized/financial/{symbol}.jsonl.gz`
- 公告：`data/normalized/announcements/{symbol}.jsonl`
- 新闻：`data/normalized/news/{symbol-or-GLOBAL}.jsonl`
- 股吧/社区：`data/normalized/forum/{symbol}.jsonl`
- 运行索引：`data/state/catalog.sqlite`
- 门面脚本最近结果：`data/state/*_daily_last_run.json`
- 全 A 股票池元数据：`data/state/eastmoney_symbols.json`
- 新闻 checkpoint：`data/state/news_pool_checkpoint.json`
- 东财股吧 checkpoint：`data/state/eastmoney_guba_checkpoint*.json`
- 东财股吧数据库：`data/state/eastmoney_guba.sqlite`

## 门面脚本输出

`*_daily.py` 必须输出 JSON 对象，至少包含：

- `ok`：本次运行整体是否成功。
- `mode`：运行模式，例如 `plan`、`batch`、`symbols`、`all`。
- `started_at`、`finished_at`：运行时间。
- `record_count`：本次写入或返回记录数；`plan` 模式可没有。
- `last_run_path`：最近一次运行摘要路径。
- `results` 或 `result`：低层 `FetchResult` 摘要。

如果失败，必须包含 `error`。

## 东财股吧 SQLite 表

`guba_posts` 以 `post_id` 为主键，保存列表页帖子明细，适合每日重复刷新互动计数字段。重要列：

- `symbol`、`post_id`、`source_url`、`title`、`author_id`、`author_name`
- `publish_time`、`publish_date`、`last_time`、`display_time`
- `click_count`、`comment_count`、`forward_count`、`top_status`
- `post_type`、`post_state`、`has_pic`、`has_video`、`bullish_bearish`
- `stockbar_name`、`raw_json`、`fetched_at`、`updated_at`

`guba_daily_stats` 以 `(symbol, trade_date)` 为主键，保存每日聚合：

- `post_count`、`total_clicks`、`total_comments`、`total_forwards`
- `hot_post_count`、`top_post_count`、`pic_post_count`、`video_post_count`
- `unique_author_count`、`bullish_bearish_0_count`、`bullish_bearish_1_count`、`bullish_bearish_2_count`
- `max_clicks`、`max_comments`、`updated_at`

## 股票池元数据

`data/input/symbols.txt` 是实际运行股票列表，每行一个 6 位代码，可包含 `#` 注释。

`data/state/eastmoney_symbols.json` 保存股票池刷新元数据：

- `generated_at`：本地 ISO 时间戳。
- `source`：`eastmoney_clist` 或 `akshare_stock_info_a_code_name`。
- `include_bj`：是否包含北交所。
- `symbol_count`：写入股票数。
- `symbols`：`{symbol, name, market, pool}` 列表。

## Checkpoint

- `data/state/news_pool_checkpoint.json`：记录全局新闻源完成状态。
- `data/state/eastmoney_guba_checkpoint.json`：记录非分批东财股吧完成股票。
- `data/state/eastmoney_guba_checkpoint_b{batch_size}_{batch_index}.json`：记录分批东财股吧完成股票。
- `data/state/eastmoney_guba_daily_{run_id}.json`：`guba_daily.py --all` 的日级 batch checkpoint。

东财股吧 checkpoint 的签名包含股票列表、页数、日期窗口和 SQLite 写入模式。命令参数不匹配时不会复用旧 checkpoint。