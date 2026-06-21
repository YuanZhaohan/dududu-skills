# A股数据源 Skill 项目跟踪

最后更新：2026-06-21

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

- 共 79 个新闻源。
- 76 个 RSS 源。
- 3 个 JSON API 源。

已实现：

- 从 `data/input/news_sources.yaml` 读取新闻源配置。
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