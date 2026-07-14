---
name: a-share-data-sources
description: A股金融数据源生产层。用于免费获取、刷新、标准化、落库和排查 A 股行情、财务、公告、新闻、股吧舆情数据；包含腾讯行情、巨潮资讯、东方财富、AkShare、BaoStock、RSS/API 新闻源、NewsNow 热榜和本地增量存储。
---

# A股数据源

这个 skill 是 A 股研究的数据源生产层。它不做交易策略、预测或投资建议，也不能编造缺失数据。

## 基本流程

1. 先判断数据类型：`market`、`financial`、`announcements`、`news`、`forum`。
2. 优先使用 `scripts/*_daily.py` 门面脚本；这些脚本是给低能力模型和外部 runner 用的稳定接口。
3. 只有调试或扩展 provider 时，才直接改 `scripts/*_data.py` 或 `scripts/sources/`。
4. 每次运行都要保留原始响应、标准化结果、运行状态和错误信息。
5. 默认只使用免费、无 key、无 cookie 的公开数据源。付费源和登录态源必须显式 opt-in。

## 稳定门面脚本

这些脚本是生产默认入口，stdout 输出机器可读 JSON，并写入 `data/state/*_last_run.json`。

- `scripts/market_daily.py`：行情日更门面，默认使用腾讯行情，支持批次和 `--plan`。
- `scripts/financial_daily.py`：财务数据日更门面，默认使用 AkShare 财务摘要，支持批次和 `--plan`。
- `scripts/announcements_daily.py`：公告日更门面，默认使用巨潮资讯 CNINFO，支持批次和 `--plan`。
- `scripts/news_daily.py`：全局新闻日更门面，支持 `--plan`，默认使用新闻池 checkpoint 续跑。
- `scripts/guba_daily.py`：东方财富股吧日更和舆情日报单文件门面，支持全 A 批次、单股票测试、断点续跑和 `--report-only` 只重算日报。

最常用命令：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\market_daily.py --plan
D:\anaconda\python.exe .\a-share-data-sources\scripts\financial_daily.py --plan
D:\anaconda\python.exe .\a-share-data-sources\scripts\announcements_daily.py --plan
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_daily.py --plan
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --plan
```

单股票探针：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\market_daily.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\financial_daily.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\announcements_daily.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --refresh-symbols never --pages 1 600519
```

按批次跑：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\market_daily.py --batch 0
D:\anaconda\python.exe .\a-share-data-sources\scripts\financial_daily.py --batch 0
D:\anaconda\python.exe .\a-share-data-sources\scripts\announcements_daily.py --batch 0
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 0
```

## 股吧日更接口

只使用 `scripts/guba_daily.py`。不要再让模型拼 `news_data.py` 参数，也不要再新增独立股吧日报脚本。

- 查看批次：`D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --plan`
- 跑完整全 A：`D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py`
- 跑单批：`D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 0`
- 只重算日报：`D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --report-only --date 2026-07-03`
- 提高热门股覆盖：加 `--pages 10` 或更高。

普通续跑不要加 `--reset`。OpenClaw 中断后重跑同一条命令即可。

`guba_daily.py` 一个文件同时负责四件事：全 A 股票池、东财股吧列表抓取、SQLite/JSONL 落库、每日舆情报告。无参数默认跑完整全 A 并在抓取成功后生成当天日报；`--no-report` 可只抓取不生成日报。

股票池优先使用东方财富 quote-list；如果东财随机断连，自动兜底 AkShare `stock_info_a_code_name()`。

## 低层脚本

这些脚本保留给调试和扩展使用：

- `scripts/market_data.py`：行情低层入口。
- `scripts/financial_data.py`：财务低层入口。
- `scripts/announcements.py`：公告低层入口。
- `scripts/news_data.py`：新闻池和社区数据混合低层入口。
- `scripts/update_all.py`：旧批处理入口。
- `scripts/smoke_test.py`：快速健康检查。
- `scripts/repair_outputs.py`：输出文件维护工具。

## 新闻续跑

`scripts/news_daily.py` 和 `scripts/news_data.py` 使用 `data/state/news_pool_checkpoint.json` 记录已完成新闻源。

如果 OpenClaw 或外部 runner 中断，重跑同一命令即可跳过已完成源，只重试失败或超时源。只有全部配置源完成后，checkpoint 才会自动清理。

当前新闻池有 91 个新闻源：77 个 RSS、3 个普通 JSON API、11 个 NewsNow/TrendRadar 热榜 API。东方财富股吧和九阳公社是独立社区源，不算在新闻池内部。

## 数据布局

- 原始响应：`data/raw/{section}/{source}/{symbol-or-global}/{timestamp}.json.gz`
- 行情标准化：`data/normalized/market/{symbol}.parquet`，缺少依赖时回退到 `.jsonl.gz`
- 文本/事件标准化：`data/normalized/{section}/{symbol}.jsonl`
- 财务标准化：`data/normalized/financial/{symbol}.jsonl.gz`
- 运行目录索引：`data/state/catalog.sqlite`
- 门面脚本最近结果：`data/state/*_daily_last_run.json`
- 全 A 股票池元数据：`data/state/eastmoney_symbols.json`
- 新闻 checkpoint：`data/state/news_pool_checkpoint.json`
- 东财股吧 checkpoint：`data/state/eastmoney_guba_checkpoint*.json`
- 东财股吧 SQLite：`data/state/eastmoney_guba.sqlite`
- 东财股吧抓取最近结果：`data/state/eastmoney_guba_daily_last_run.json`
- 东财股吧日报最近结果：`data/state/eastmoney_guba_report_last_run.json`
- 东财股吧舆情日报：`data/state/guba_sentiment/guba_sentiment_{date}.json`

## 参考文档

- 改 provider 优先级前读 `references/source-map.md`。
- 改 fallback、retry、cache 前读 `references/provider-patterns.md`。
- 改输出 schema 或路径前读 `references/data-contract.md`。
- 改舆情/社区源前读 `references/forum-sentiment-sources.md`。
- 参考项目来源说明见 `references/imported-sources.md`。
- 原始参考项目放在 `references/UZI-Skill/`、`references/TradingAgents/`、`references/ai-hedge-fund/`、`references/TrendRadar/`。这些目录保留原貌，不作为当前 skill 的入口。

## 规则

- 不能静默吞掉 provider 失败，必须记录 attempt 和 error。
- 不能用弱 fallback 覆盖更强的已有数据。
- 写 JSON 前必须清理 `NaN`、`Infinity` 等非标准值。
- 不要为了修增量问题删除 `data/` 或 cache，除非用户明确要求重建。
- 不要把 API key、SMTP 密码、cookie、token 或本地私有配置写入仓库。
