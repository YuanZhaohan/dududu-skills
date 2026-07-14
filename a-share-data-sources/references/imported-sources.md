# 参考项目来源

当前 skill 是生产实现。以下目录只是参考源码，不要运行它们的安装脚本，也不要把私有配置复制进 `a-share-data-sources`。

## `金融数据源_v2`

原始本地路径：`C:\Users\dududu\Desktop\金融数据源_v2`。

用途：第四个参考项目。

- `sources.yaml`：已清洗进 `data/input/news_sources.yaml`，只保留公开 RSS/API 新闻源定义。
- `track-pulse/fetcher.py`：参考 direct-first 新闻抓取、retry、timeout、本地 cache fallback。
- `industry-sentiment-tracker/scripts/industry_stock_pipeline.py`：参考腾讯行情批量、5 日动量、东财股吧帖子抽取、行业聚合。
- `industry-sentiment-tracker/scripts/industry_sentiment_report.py`：情绪报告可作为下游参考，但 LLM 调用不属于当前数据源层。

不要复制 API key、SMTP 密码或本地用户配置。

## `references/UZI-Skill/`

用途：参考 A 股源优先级和数据源坑点。

- AkShare 覆盖广，但稳定性不能作为唯一依赖。
- BaoStock 适合 HTTPS 源失败时兜底。
- CNINFO 直连第一页接口比 AkShare 巨潮全分页包装更安全。
- 金十、东财、同花顺、雪球、论坛/browser fallback 都可作为源模式参考。

常看路径：

- `references/UZI-Skill/docs/DATA-PROVIDERS.md`
- `references/UZI-Skill/commands/analyze-stock.md`
- `references/UZI-Skill/skills/deep-analysis/scripts/lib/`

## `references/TradingAgents/`

用途：参考路由行为。

- 分数据类型 vendor map。
- 显式 no-data 状态。
- rate limit 或 provider 特定失败时继续 fallback。

常看路径：

- `references/TradingAgents/tradingagents/dataflows/`
- `references/TradingAgents/tradingagents/agents/analysts/`
- `references/TradingAgents/tradingagents/graph/`

## `references/ai-hedge-fund/`

用途：参考生产卫生。

- 类型化数据记录。
- 按 endpoint 和日期划分 cache 边界。
- rate limit retry/backoff。

常看路径：

- `references/ai-hedge-fund/src/`
- `references/ai-hedge-fund/app/backend/`

## `references/TrendRadar/`

来源：https://github.com/sansan0/TrendRadar

用途：参考趋势和舆情监控模式。

已集成的 NewsNow 热榜源定义位于 `data/input/news_sources.yaml`，类型是 `newsnow_hotlist`；解析仍放在 `scripts/sources/news_pool.py`，不单独新增 provider 文件。

可参考内容：

- 多平台热榜聚合。
- 下游 AI 分析前的关键词过滤和降噪。
- RSS 订阅和定时轮询。
- 微信、飞书、钉钉、Telegram、email、ntfy、Bark、Slack 等提醒路由。
- MCP 风格的趋势/新闻查询接口。

不要复制通知 token、本地 `.env` 或生成的 `output/` 数据库。