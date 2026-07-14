# A股数据源 Skill 项目跟踪

最后更新：2026-07-03

## 项目目标

构建一个偏生产可用的 Codex skill，用来维护免费的 A股金融数据源，包括采集、标准化、本地存储和后续分析工作流。

当前重点是数据源层，不是策略、预测或交易建议。

覆盖范围：

- 行情数据
- 财务报表和财务指标
- 公告
- 金融新闻
- 股吧、社区、舆情数据

## 当前状态

整体状态：可用原型，已经接近生产基线。

当前主 skill：

- `a-share-data-sources`

当前真实仓库位置：

- `D:\gitee_project\dududu-skills`

当前 GitHub 仓库：

- `https://github.com/YuanZhaohan/dududu-skills.git`

目录结构已经整理完成：

- `SKILL.md`：当前 skill 的入口说明。
- `scripts/`：按数据类型拆分的生产入口脚本。
- `scripts/sources/`：单个数据源的 provider 脚本。
- `data/input/`：输入配置，例如股票列表、新闻源池。
- `references/`：设计说明和参考项目源码。

参考项目已经收进当前 skill：

- `references/UZI-Skill/`
- `references/TradingAgents/`
- `references/ai-hedge-fund/`
- `references/TrendRadar/`

运行产物不会上传到 git：

- `data/raw/`
- `data/normalized/`
- `data/state/`

## 已实现能力

### 行情数据

入口脚本：`scripts/market_data.py`

当前 provider：

- `tencent_quote`

已实现：

- 获取实时行情字段。
- 获取日线 K 线和短期动量。
- 按股票代码标准化记录。
- 行情数据压缩存储。
- 优先使用 Parquet + zstd；缺少依赖时回退到 `.jsonl.gz`。

后续计划：

- 增加 AkShare 历史行情补充。
- 增加 efinance 冗余源。
- 增加 BaoStock 作为历史数据兜底。

### 财务数据

入口脚本：`scripts/financial_data.py`

当前 provider：

- `akshare_financial`

已实现：

- 在 AkShare 可用时获取财务摘要和财务指标类数据。
- 空结果会被视为失败，不会静默成功。
- 标准化结果保存为 `.jsonl.gz`。

后续计划：

- 增加 BaoStock 季度财务数据兜底。
- 增加 Eastmoney 直连财务表。

### 公告数据

入口脚本：`scripts/announcements.py`

当前 provider：

- `cninfo`

已实现：

- 直连巨潮资讯 CNINFO 公告接口。
- 支持 orgId 查询。
- 限制分页，避免全量分页拖死。
- 发布时间标准化。
- 空结果会被视为失败。

后续计划：

- 增加 Eastmoney 公告冗余源。

### 新闻数据

入口脚本：`scripts/news_data.py`

当前 provider 组：

- `news_pool`

当前新闻源池：

- 共 91 个新闻源。
- 77 个 RSS 源。
- 3 个普通 JSON API 源。
- 11 个 NewsNow/TrendRadar 热榜 API 源。

已实现：

- 从 `data/input/news_sources.yaml` 读取新闻源配置。
- 集成 TrendRadar/NewsNow 热榜源，不新增独立脚本，仍然走 `news_pool.py`。
- 支持并发抓取。
- 支持 timeout、retry、worker 数量、整体 deadline。
- 支持断点续跑。
- 已完成的 provider 会被跳过。
- 失败或超时的 provider 会在下次运行时重试。
- 单个 provider 失败不会导致整个脚本崩掉。
- 新闻文本类数据保存为 JSONL，方便人工检查。

关键状态文件：

- `data/state/news_pool_checkpoint.json`

当前规则：

- 只有所有配置的新闻源都成功完成后，checkpoint 才会自动清理。
- 如果 OpenClaw 或其他外部 runner 中断，重新跑同一命令即可续跑。

### 股吧和舆情数据

入口脚本：`scripts/news_data.py`

当前默认 provider：

- `eastmoney_guba`
- `jiuyangongshe`

已实现：

- 东方财富股吧公开页面抓取。
- 九阳公社公开搜索页和文章链接抓取。
- 按股票代码保存舆情记录。
- 默认路径不需要 key、不需要 cookie。
- 标准化结果保存为 JSONL。

暂不默认启用：

- 雪球
- 淘股吧
- 集思录

当前判断：

- 雪球和淘股吧更适合做成显式 opt-in 数据源。
- 这类来源通常需要 cookie、浏览器 session 或登录状态。
- cookie、token、登录态不能写进 skill 仓库。

## 稳健性设计

已经完成：

- 每个 provider 拆成独立脚本，方便后续维护。
- 每个数据类型有独立入口脚本。
- 支持 provider 级别的 retry 和 timeout。
- 新闻支持 checkpoint 断点续跑。
- 使用确定性的 `record_key` 做去重。
- 原始响应保存为不可变 `.json.gz`。
- 标准化数据使用 Parquet、JSONL 或 `.jsonl.gz`。
- SQLite catalog 用于记录运行元数据。
- 写 JSON 前会清理 `NaN`、`Infinity` 等非标准值。
- 有 `repair_outputs.py` 维护脚本。
- 有 `smoke_test.py` 用于快速健康检查。
- `.gitignore` 已排除运行产物、缓存和本地密钥。

## 常用命令

从仓库根目录运行：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\market_data.py 600519 000001
D:\anaconda\python.exe .\a-share-data-sources\scripts\financial_data.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\announcements.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\smoke_test.py
```

新闻断点续跑：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --timeout 5 --retries 2 --deadline 30 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --reset-checkpoint 600519
```

编译检查：

```powershell
D:\anaconda\python.exe -m py_compile .\a-share-data-sources\scripts\*.py .\a-share-data-sources\scripts\sources\*.py
```

Git 状态检查：

```powershell
git -c safe.directory=D:/gitee_project/dududu-skills status --short --branch
```

## 已知风险

- 免费公开数据源随时可能改页面结构或限流。
- AkShare 覆盖很广，但稳定性不能作为唯一依赖。
- CNINFO 必须保持有限分页，避免全量分页拖慢甚至卡死。
- RSS 源由外部网站控制，可能失效、改地址或返回空内容。
- 股吧和社区页面 HTML 变化较快，解析逻辑需要定期检查。
- 雪球直连 API 通常需要 cookie 或登录态。
- 当前还没有正式的数据质量仪表盘。
- 当前还没有配置 CI 或定时任务。

## 下一阶段计划

### P0：优先做

- 定期运行 `smoke_test.py`，记录各 provider 健康状态。
- 从 `catalog.sqlite` 生成 provider 健康报告。
- 维护新闻源池，移除长期死亡源。
- 增加 BaoStock 财务和历史行情兜底。

### P1：增强生产可用性

- 增加 Eastmoney 公告兜底。
- 增加 Eastmoney 直连财务表。
- 增加雪球 opt-in browser/cookie provider。
- 增加可选舆情源配置文件。
- 给四类入口脚本统一 CLI 参数风格。

### P2：后续扩展

- 增加数据质量检查：空结果率、重复率、过期率、schema 漂移。
- 增加每日增量调度。
- 增加行业层面聚合。
- 把情绪打分做成独立下游模块。

## 定期复盘检查清单

每次回顾时检查：

- 各数据类型脚本是否能正常跑完？
- 哪些 provider 失败、超时或返回空数据？
- 是否有数据源字段结构变化？
- 标准化文件是否正确去重？
- 新闻 checkpoint 是否能在中断后续跑？
- git 是否误暂存了运行产物？
- 仓库里是否误出现 cookie、token、本地密钥？
- 新的数据源决策是否同步到 `references/source-map.md`？
- provider 行为变化是否同步到 `references/provider-patterns.md`？

## 复盘记录模板

以后每次定期回顾可以复制这个模板：

```markdown
## YYYY-MM-DD 复盘

### 总结

-

### 本次运行命令

-

### 数据源健康状态

- 行情：
- 财务：
- 公告：
- 新闻：
- 舆情：

### 新发现的问题

-

### 本次决策

-

### 下一步动作

-
```
## 2026-06-22 更新：东财股吧舆情数据库

### 本次完成

- 将 `eastmoney_guba` 默认层升级为列表页全字段抓取，仍然不默认进入详情页、不默认抓评论正文。
- 新增本地 SQLite：`data/state/eastmoney_guba.sqlite`。
- 新增 `guba_posts` 表：按 `post_id` upsert，适合每天重复跑，刷新点击数、评论数、转发数等变化字段。
- 新增 `guba_daily_stats` 表：按 `symbol + trade_date` 聚合每日发帖数、点击数、评论数、转发数、热帖数、置顶数、图文/视频数、作者数等。
- `news_data.py` 新增 `--skip-global-news`，可以只跑股吧/社区，不必每天顺带跑完整新闻池。
- 保留原有 JSONL 输出：`data/normalized/forum/{symbol}.jsonl`，数据库是增强存储，不破坏旧流程。

### 日更命令

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --skip-global-news --skip-jiuyangongshe --forum-pages 3 600519 000001
```

### 历史回补命令

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --skip-global-news --skip-jiuyangongshe --forum-pages 50 --forum-start-date 2026-01-01 --forum-end-date 2026-06-22 600519
```

### 验证结果

- 编译通过：`eastmoney_guba.py`、`news_data.py`。
- 实测 `600519` 一页抓取成功，写入 `guba_posts` 和 `guba_daily_stats`。
- 当前评论正文不放默认路径；只默认使用稳定的 `post_comment_count`。

## 2026-06-30 更新：全 A 股东财股吧日更链路

### 本次完成

- `eastmoney_guba.py` 新增东方财富 quote-list 股票池刷新能力，可从公开接口生成 `data/input/symbols.txt`，并把名称、市场、来源池保存到 `data/state/eastmoney_symbols.json`。
- `news_data.py` 新增 `--refresh-symbols`，用于日更前刷新全 A 股票池；默认包含沪深 A 股和北交所，必要时可用 `--exclude-bj` 排除北交所。
- `news_data.py` 新增 `--batch-size` 和 `--batch-index`，支持把全 A 股票池切成固定大小批次跑，便于 OpenClaw 或定时任务分段执行。
- 东财股吧新增 per-symbol checkpoint：全量运行使用 `data/state/eastmoney_guba_checkpoint.json`，分批运行使用 `data/state/eastmoney_guba_checkpoint_b{batch_size}_{batch_index}.json`。
- checkpoint 记录已经成功完成的股票；同一命令重跑时会跳过已完成股票，只重试未完成或失败股票；所选股票全部完成后自动清理 checkpoint。
- 保留原有 SQLite 落库和 JSONL 输出：帖子明细进 `guba_posts`，每日聚合进 `guba_daily_stats`，人工检查仍看 `data/normalized/forum/{symbol}.jsonl`。

### 生产命令

刷新全 A 股票池并只跑第一只股票做探针：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --refresh-symbols --skip-global-news --skip-jiuyangongshe --forum-pages 1 --batch-size 1 --batch-index 0
```

日更全 A 股第 0 批，每批 500 只：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --skip-global-news --skip-jiuyangongshe --forum-pages 3 --batch-size 500 --batch-index 0
```

日更全 A 股第 1 批，每批 500 只：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\news_data.py --skip-global-news --skip-jiuyangongshe --forum-pages 3 --batch-size 500 --batch-index 1
```

如果 OpenClaw 中断，原命令直接再跑一遍即可。不要加 `--reset-guba-checkpoint`，否则会丢掉已完成股票的断点。

### 验证结果

- 编译通过：`eastmoney_guba.py`、`news_data.py`、`news_pool.py`。
- 股票池小样本验证通过：`fetch_a_share_symbols(max_pages=1)` 返回 200 条样本记录，包含沪深 A 和北交所样本；名称在 Unicode 层面正常，终端乱码只是 PowerShell 编码显示问题。
- 股票池全量只读验证通过：收紧北交所参数为 `m:0+t:81+s:2048` 后，当前返回 5724 只，其中沪深 5390 只、北交所 334 只，避免把新三板宽池混入默认全 A 股票池。
- 股吧小样本验证通过：`600519` 单页抓取返回 77 条记录，成功写入 raw、normalized JSONL 和 `eastmoney_guba.sqlite`。
- 分批 checkpoint 清理验证通过：`--batch-size 1 --batch-index 0` 跑完后，对应 `eastmoney_guba_checkpoint_b1_0.json` 自动清理。

### 当前边界

- 现在能支持“每天按批次获取所有股票的东财股吧列表页帖子数据”。
- 当前默认层仍不抓帖子详情页正文和评论正文，只抓列表页稳定字段、互动计数和原始列表 JSON。
- 想回补历史存量时，需要拉大 `--forum-pages` 并设置 `--forum-start-date`、`--forum-end-date`；东财列表页能翻到多深取决于公开页面实际返回，不保证无限历史。

## 2026-06-30 更新：股吧稳定接口门面

### 本次完成

- 新增 `scripts/guba_daily.py`，作为东财股吧日更的唯一推荐入口。
- 默认只跑 `eastmoney_guba`，不跑全局新闻池，不跑九阳公社，避免模型拼错 `news_data.py` 的组合参数。
- 默认写入 `data/state/eastmoney_guba.sqlite`，保留原有 raw 和 normalized JSONL 输出。
- 支持 `--plan`、`--all`、`--batch N`、单股票参数四种调用方式。
- `--all` 额外维护日级 batch checkpoint：`data/state/eastmoney_guba_daily_{run_id}.json`，中断后重跑同一命令会跳过已完成 batch。
- 股票池优先使用东财 quote-list；如果东财随机断连，自动兜底到 AkShare `stock_info_a_code_name()`。
- 每次运行都会写 `data/state/eastmoney_guba_daily_last_run.json`，方便外部 runner 或模型读取最近一次结果。

### 最笨模型也能用的接口

查看今天需要多少批：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --plan
```

一条命令跑完整全 A 股吧日更：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --all
```

只跑第 0 批：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 0
```

只测试一只股票：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --refresh-symbols never --pages 1 600519
```

### 验证结果

- `guba_daily.py --refresh-symbols never --pages 1 600519` 实测成功，返回 74 条 `600519` 当日列表页记录，并写入 SQLite。
- `guba_daily.py --plan` 实测成功，在东财 quote-list 断连时自动使用 AkShare 股票池，返回 5527 只、12 批，并保持 stdout 为干净 JSON。

### 接口边界

- 默认 `--pages 3`，覆盖东财股吧公开列表页前三页；热门股票想抓得更全，需要提高到 `--pages 10` 或更高。
- 普通断点续跑不要加 `--reset`。
- 这个接口仍然不抓帖子详情正文和评论正文，默认只抓列表页稳定字段与互动计数。

## 2026-07-01 更新：统一门面脚本和中文文档

### 本次完成

- 新增 `scripts/market_daily.py`，作为行情数据稳定门面，支持 `--plan`、`--batch N`、单股票探针，最近结果写入 `data/state/market_daily_last_run.json`。
- 新增 `scripts/financial_daily.py`，作为财务数据稳定门面，支持 `--plan`、`--batch N`、单股票探针，最近结果写入 `data/state/financial_daily_last_run.json`。
- 新增 `scripts/announcements_daily.py`，作为公告数据稳定门面，支持 `--plan`、`--batch N`、单股票探针，最近结果写入 `data/state/announcements_daily_last_run.json`。
- 新增 `scripts/news_daily.py`，作为全局新闻稳定门面，默认复用新闻池 checkpoint，最近结果写入 `data/state/news_daily_last_run.json`。
- 保留 `scripts/guba_daily.py` 作为股吧稳定门面。
- 将活跃 Markdown 文档改写为中文：`SKILL.md`、`references/source-map.md`、`references/provider-patterns.md`、`references/data-contract.md`、`references/forum-sentiment-sources.md`、`references/imported-sources.md`。
- 保留第三方参考项目目录下的 Markdown 原文，不翻译 `references/UZI-Skill/`、`references/TradingAgents/`、`references/ai-hedge-fund/`、`references/TrendRadar/`，避免破坏原始参考材料。

### 最笨模型也能用的统一入口

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

### 当前边界

- 门面脚本统一了调用方式，但底层 provider 的可用性仍受公开网站、网络、限流和字段变更影响。
- 财务和公告门面当前没有单独 checkpoint；它们依赖幂等写入和 catalog 记录，后续如果要跑全 A 长任务，可以再加 batch 级 checkpoint。
- 第三方参考项目 Markdown 暂不翻译，作为源码参考保留原貌。

## 2026-07-02 进度记录：统一门面验证与待收尾

### 已完成

- 新增共享工具 `scripts/daily_utils.py`，供行情、财务、公告门面复用股票池解析、批次切分、JSON 状态写入等逻辑。
- `scripts/market_daily.py`、`scripts/financial_daily.py`、`scripts/announcements_daily.py` 已统一成和 `guba_daily.py` 类似的稳定门面：支持 `--plan`、`--batch/--batch-index`、`--batch-size`、显式单股票参数、统一 JSON 输出和 `data/state/*_daily_last_run.json`。
- `scripts/news_daily.py` 已补齐 `--plan`，可以只读取新闻源配置并输出源数量、分类数量、checkpoint 路径，不触发实际抓取。
- `data/input/news_sources.yaml` 中明显的中文名称乱码已修复，避免后续新闻记录里的 `source_name` 带脏字段。
- 活跃 Markdown 文档已经改为中文；第三方 reference 项目的 Markdown 保留原文，不破坏参考源码。

### 已验证

- Python 编译检查通过：`daily_utils.py`、四个 `*_daily.py`、`guba_daily.py`、`market_data.py`、`financial_data.py`、`announcements.py`、`news_data.py`。
- 本地计划模式通过：
  - `market_daily.py --plan --refresh-symbols never`
  - `financial_daily.py --plan --refresh-symbols never`
  - `announcements_daily.py --plan --refresh-symbols never`
- 新闻计划模式通过：`news_daily.py --plan` 返回 91 个新闻源，其中 77 个 RSS、14 个 API 源；API 源里 3 个普通 JSON、11 个 NewsNow/TrendRadar 热榜。
- 股吧计划模式通过：`guba_daily.py --plan` 当前实时股票池返回 5572 只、12 批，stdout 为干净 JSON。
- `market_daily.py --plan` 在股票池联网刷新异常时不再直接崩溃；现在会回退到本地 `symbols.txt`，并在 `symbol_refresh` 中写明 `fallback_to_local: true` 和原始错误。

### 当前待验证 / 阻塞

- 行情、财务、公告的单股票真实抓取探针在当前沙箱内触发 `OperationalError: attempt to write a readonly database`，原因是脚本需要写 `data/state/catalog.sqlite`，而当前执行沙箱对真实仓库路径写 SQLite 受限。
- 这不是门面参数解析问题；需要在允许真实写入仓库的执行权限下重跑以下探针：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\market_daily.py --refresh-symbols never --skip-momentum 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\financial_daily.py --refresh-symbols never 600519
D:\anaconda\python.exe .\a-share-data-sources\scripts\announcements_daily.py --refresh-symbols never --page-size 5 600519
```

### 下一步

- 用真实写入权限重跑三条单股票探针，确认底层 provider 写 raw、normalized、catalog 的链路。
- 如果三条探针通过，再跑 `git diff --check` 和一次最终 `git status`。
- 如果财务或公告 provider 返回空数据，需要记录为空结果还是接口失败，再决定是否加 fallback。
- 后续所有多步骤改动都同步更新本文件，避免只在聊天里记录进度。

## 2026-07-02 进度记录：全量股吧日更与舆情跟踪

### 需求

- 每天抓全量 A 股东方财富股吧帖子。
- 每天记录抓取耗时，包括开始时间、结束时间、总耗时、股票数、批次数、失败批次。
- 每天生成舆情跟踪结果，包括全市场汇总、单股票汇总、正面/负面/中性倾向、热门帖子和较昨日变化。

### 设计决策

- 股吧抓取、SQLite 落库、断点续跑和舆情日报统一收敛到 `scripts/guba_daily.py` 一个文件。
- 不再保留独立股吧日报脚本作为模型入口；`--report-only` 用已有 SQLite 只重算日报，不重新抓网页。
- 先用标题关键词 + 东财列表页 `bullish_bearish` 字段做轻量情绪跟踪；后续如果要更准，再接大模型或中文情绪模型。

### 已完成

- `scripts/guba_daily.py` 已作为股吧唯一推荐入口：负责全 A 股票池、分批、断点续跑、SQLite 落库、JSONL 输出和舆情日报。
- `guba_daily.py` 输出已增加 `duration_seconds`，并在 `data/state/eastmoney_guba_daily_last_run.json` 记录最近一次抓取开始时间、结束时间、耗时、股票数、批次数、失败批次。
- `guba_daily.py --report-only` 默认写入 `data/state/guba_sentiment/guba_sentiment_{date}.json`，同时 stdout 输出同一份 JSON。
- `report-only` 的最近运行状态单独写 `data/state/eastmoney_guba_report_last_run.json`，不再覆盖抓取耗时状态。

### 验证结果

- 编译通过：`guba_daily.py`、`eastmoney_guba.py`。
- `guba_daily.py --plan --refresh-symbols never` 已返回 `duration_seconds`。
- 用现有 2026-06-30 数据生成样例报告成功：`data/state/guba_sentiment/guba_sentiment_2026-06-30.json`。
- 样例报告中，`600519` 当日帖子 149 条，标题关键词口径为轻微正面：正面 15、负面 11、中性 123，`sentiment_score=0.1538`。

### 生产命令

先看今天全 A 要分多少批：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --plan
```

每天全量抓取并生成当天舆情日报。无参数默认就是全 A 串行全量：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py
```

更稳的 OpenClaw/定时任务方式：按批次拆开跑，断了重跑同一批即可：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 0 --batch-size 500 --pages 3
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 1 --batch-size 500 --pages 3
```

只用已有 SQLite 重算某天舆情日报：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --report-only --date 2026-07-02
```

### 当前边界

- 现在的正负面是标题关键词轻量口径，适合每日跟踪趋势，不等于精细语义分类。
- 东财列表页 `bullish_bearish` 字段会原样统计保留，但暂不单独作为最终正负面判断。
- 全量耗时需要跑完一轮后由 `eastmoney_guba_daily_last_run.json` 的 `duration_seconds` 给出；首次全量前只能通过小批次耗时估算。

## 2026-07-03 进度记录：股吧入口合并为单文件

### 本次完成

- 股吧模型调用入口收敛到 `scripts/guba_daily.py`，一个 py 覆盖抓取、落库、断点续跑和舆情日报。
- `scripts/` 目录当前只保留一个股吧入口脚本：`guba_daily.py`。
- `--report-only` 使用 `eastmoney_guba_report_last_run.json` 记录报告运行，不覆盖 `eastmoney_guba_daily_last_run.json` 的抓取耗时。
- 日报读取抓取状态时会忽略历史遗留的 `mode=report_only`，避免把报告运行误判成全量抓取。

### 已验证

- 编译通过：`guba_daily.py`、`eastmoney_guba.py`。
- `guba_daily.py --help` 通过，参数中包含 `--plan`、`--report-only`、`--batch/--batch-index`、`--no-report`、`--report-on-partial`。
- `guba_daily.py --plan --date 2026-07-03 --refresh-symbols never` 通过，本地股票池 5650 只，分 12 批，计划耗时 0.015 秒。
- `guba_daily.py --report-only --date 2026-06-30 --no-write-report --top-n 3` 通过，样例数据 149 条，轻量情绪为 positive，`sentiment_score=0.1538`。

### 仍未完成

- 2026-07-03 全 A 实际抓取已按用户要求中止，不再跑完整全流程。
- 本次后台进程 PID 42068，启动时间 2026-07-03 10:20:24，命令为 `guba_daily.py --date 2026-07-03 --report-on-partial`。
- checkpoint 截止 2026-07-03 12:15:23 已完成 5/12 批、2500/5650 只股票，无失败批次。
- 完成批次记录：第 0 批 3981 条，第 1-4 批均为 0 条。
- SQLite 当前已有 2026-07-03 当日帖子 3954 条，覆盖 190 只股票。
- 从 checkpoint 创建时间 10:21:19 到第 4 批完成时间 12:15:23，5 批耗时约 114 分钟，平均约 22.8 分钟/批。
- 按 12 批估算，`pages=3` 的 5650 只全 A 股吧全量日更约需 4.5-4.8 小时；实际时间会受网络、东财限流、热门股页数和重试影响。
- checkpoint 保留在 `data/state/eastmoney_guba_daily_78f48140129176d7.json`，后续重跑同一命令会跳过已完成批次并从未完成批次继续。
- batch 1 已完成：500 只，完成时间 11:02:27，采集记录数 0，失败批次为空；SQLite 今日统计仍为 3954 条帖子、190 只股票。
- batch 2 已启动，继续通过 provider checkpoint 续跑。
- 运行中发现重复股吧进程：PID 7224，命令为 `guba_daily.py --date 2026-07-03 --pages 3`，会与主进程写同一份 SQLite/checkpoint。
- 已停止重复进程 PID 7224，并清理其残留的未完成 `eastmoney_guba_checkpoint_b500_0.json`；保留主进程 PID 42068 继续跑。
- batch 2 已完成：500 只，完成时间 11:28:59，采集记录数 0，失败批次为空；SQLite 今日统计为 3954 条帖子、190 只股票，日聚合帖子数暂为 3979。
- batch 3 已启动，继续通过 provider checkpoint 续跑。
- batch 3 已完成：500 只，完成时间 11:53:51，采集记录数 0，失败批次为空；SQLite 今日统计仍为 3954 条帖子、190 只股票，日聚合帖子数暂为 3979。
- batch 4 已启动，继续通过 provider checkpoint 续跑。
- batch 4 已完成：500 只，完成时间 12:15:23，采集记录数 0，失败批次为空；SQLite 今日统计仍为 3954 条帖子、190 只股票，日聚合帖子数暂为 3979。
- batch 5 已启动，继续通过 provider checkpoint 续跑。
- 主进程 PID 42068 在 batch 5 的 132/500 处停止，stdout/stderr 均为 0，更像外部中断而不是脚本内异常。
- 当前断点状态：日级 checkpoint 已完成 batch 0-4；provider checkpoint `eastmoney_guba_checkpoint_b500_5.json` 保留 batch 5 已完成 132/500，失败 0。
- 续跑策略：使用 `guba_daily.py --date 2026-07-03 --report-on-partial --refresh-symbols never`，避免股票池刷新导致 run_id 变化。