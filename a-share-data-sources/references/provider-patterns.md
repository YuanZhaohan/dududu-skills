# Provider 行为规范

## Fallback

- 每个数据类型维护自己的 fallback 链，不使用一个全局顺序。
- 只有 timeout、空结果、结构解析失败、必需字段缺失时才触发 fallback。
- 每次尝试都要写入 `data/state/catalog.sqlite`。
- 不能用弱 fallback 覆盖更强的已有记录。

## 增量更新

- 每条标准化记录都必须有确定性的 `record_key`。
- 同一刷新命令重复运行不能产生重复记录。
- 不要通过删除 cache 或标准化输出修复增量问题；只有用户明确要求重建时才重建。
- 批处理时，一个股票失败不能阻塞其他股票。
- 东方财富股吧按命令签名维护 per-symbol checkpoint；重跑会跳过已完成股票，只重试未完成或失败股票。
- 门面脚本必须输出机器可读 JSON，并写入 `data/state/*_daily_last_run.json`。

## 存储

- 原始响应是不可变 `.json.gz` 文件。
- SQLite 记录运行元数据、状态、record_key、路径和错误；不要把大段正文都塞进 catalog。
- 行情数据尽量按 key 压缩合并后写 Parquet。
- 新闻、公告、股吧帖子保留 JSONL，方便人工检查。

## 参考项目沉淀

- `references/UZI-Skill/`：A 股数据源优先级、CNINFO 慢路径规避、BaoStock 兜底经验。
- `references/TradingAgents/`：vendor 路由、显式 no-data 状态、避免编造数据。
- `references/ai-hedge-fund/`：类型化记录、缓存边界、429 retry/backoff。
- `金融数据源_v2`：RSS/API 新闻池、direct-first/cache-fallback 新闻抓取、腾讯行情批量、东财股吧舆情。
- `references/TrendRadar/`：多平台热榜聚合、关键词过滤、定时轮询、提醒路由、MCP 查询接口形态。