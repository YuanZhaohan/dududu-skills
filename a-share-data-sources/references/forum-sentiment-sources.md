# 股吧与舆情数据源

## 默认生产源

默认源必须无 key、无 cookie，并且失败时能被记录，不影响其他源继续运行。

1. `eastmoney_guba`
   - 范围：按股票抓取东方财富股吧列表页。
   - 当前字段：帖子标题、作者 id/昵称、发布时间、更新时间、展示时间、点击数、评论数、转发数、置顶状态、媒体标记、多空标记、股吧元数据、原始列表 JSON。
   - 参考项目：
     - https://github.com/zcyeee/EastMoney_Crawler
     - https://github.com/algosenses/EastMoneySpider
     - https://github.com/Fucov/EastMoneyGuBaCrawler

2. `jiuyangongshe`
   - 范围：九阳公社公开搜索页。
   - 当前用途：抓文章链接和摘要。
   - 参考项目：
     - https://github.com/jiaweif3ng/FinSpider

## 东财股吧存储

`eastmoney_guba` 在保留 JSONL 输出的同时写入 `data/state/eastmoney_guba.sqlite`：

- `guba_posts`：每个 `post_id` 一行，使用 upsert，适合每日刷新点击/评论/转发等变化字段。
- `guba_daily_stats`：按股票和日期聚合发帖数、点击数、评论数、转发数、热帖数、置顶数、媒体帖数、作者数、多空桶等。

普通日更优先使用门面脚本：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --plan
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --batch 0
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --all
```

单股票测试：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --refresh-symbols never --pages 1 600519
```

历史回补可以直接指定日期窗口和页数：

```powershell
D:\anaconda\python.exe .\a-share-data-sources\scripts\guba_daily.py --pages 50 --start-date 2026-01-01 --end-date 2026-06-22 600519
```

## 全 A 日更流程

`guba_daily.py` 是默认接口。低层 `news_data.py` 只用于调试或需要新闻和社区混合运行的场景。

股票池优先走东方财富 quote-list；如果东财断连，自动兜底 AkShare `stock_info_a_code_name()`。北交所默认约束为 `m:0+t:81+s:2048`，避免把新三板宽池混入默认全 A。

普通续跑不要用 `--reset`。只有明确要重跑同一批时才使用 `--reset`。

## 评论正文边界

默认路径不抓帖子详情正文和评论正文。东财前端存在 `reply/api/Reply/ArticleNewReplyList`，但无 cookie 直接访问可能返回 busy/error，稳定性不足。当前默认只使用更稳定的 `post_comment_count` 和列表页字段。

## 可选 cookie/browser 源

这些源有价值，但不能进入默认生产路径，除非显式 opt-in。

1. `xueqiu`
   - `/S/{symbol}/POST` 可能返回 WAF 页面。
   - 直连 JSON 评论/搜索 API 常见登录或 403。
   - 建议做成可选 browser/cookie 源。

2. `taoguba`
   - 公开页可访问，但有效论坛数据通常依赖 cookie/session。
   - 建议做成可选 cookie 源。

3. `jisilu`
   - 公开页可达，但搜索结果未必股票特异。
   - 更适合作为全局社区趋势源或搜索兜底。

## 默认拒绝项

- 雪球直连 JSON API：通常需要登录/cookie 或触发 403。
- 淘股吧直连论坛爬取：cookie 依赖强，脆弱度高于东财/九阳公社。
- Selenium-only 爬虫：可作参考，但不适合默认轻量 runner。

## 实现规则

- 默认 provider 必须无 key、无 cookie。
- cookie/browser provider 必须显式 opt-in，且不能把密钥写进仓库。
- 每个 provider 失败都必须进入 `catalog.sqlite`。
- 所有社区标准化记录统一写 `data/normalized/forum/{symbol}.jsonl`。