# 数据源地图

## 行情

生产默认路径：

1. `tencent_quote`：实时行情、市值、市盈率、换手率、日线 K 线和短期动量。
2. `akshare`：未来可选的更深历史 K 线补充。
3. `efinance`：未来可选的东方财富风格行情冗余源。
4. `baostock`：未来可选的历史行情兜底，适合 HTTPS 源不稳定时使用。

行情数据优先压缩保存。能写 Parquet + zstd 就写 Parquet；缺少 `pyarrow` 时回退到 `.jsonl.gz`。

推荐门面：`scripts/market_daily.py`。

## 财务

生产默认路径：

1. `akshare_financial`：财务摘要和财务指标表。
2. `baostock`：未来可选的季度财务兜底。
3. `eastmoney_direct`：未来可选的东方财富直连财务表。

不同财务源字段漂移较多，标准化记录保存为 `.jsonl.gz`。

推荐门面：`scripts/financial_daily.py`。

## 公告

生产默认路径：

1. `cninfo_direct`：巨潮资讯 `/new/hisAnnouncement/query`，只抓有限页，避免全量分页拖死。
2. `eastmoney`：未来可选的公告冗余源。
3. `akshare_limited`：只在明确有边界时 opt-in。

不要默认使用会先抓完整分页再返回的 AkShare 巨潮包装函数。

推荐门面：`scripts/announcements_daily.py`。

## 新闻与舆情

生产默认路径：

1. `news_pool`：从 `金融数据源_v2` 整理来的公开 RSS/API 新闻池。
2. `eastmoney_guba`：东方财富股吧列表页舆情，包含帖子 id、标题、作者、发布时间、点击/评论/转发、置顶/媒体标记、原始列表 JSON，并写入 SQLite 聚合。
3. `jiuyangongshe`：九阳公社公开搜索页和文章链接。
4. `newsnow_hotlist`：TrendRadar/NewsNow 风格热榜，覆盖微博、百度、知乎、头条、华尔街见闻、财联社、澎湃、B 站、凤凰、贴吧、抖音等。
5. UZI 风格直连新闻源：金十、东财快讯、东财公告、同花顺新闻等。
6. 可选 cookie/browser 源：雪球、淘股吧、集思录。默认不启用。
7. 可选 DDGS 搜索：只适合稀疏股票补充。

文本数据保留 JSONL，便于检查标题、摘要、链接和原始字段。

推荐门面：`scripts/news_daily.py` 和 `scripts/guba_daily.py`。