# Forum And Sentiment Sources

## Production Defaults

These sources are safe enough for the default batch path because they can return useful public data without user cookies.

1. `eastmoney_guba`
   - Scope: per-symbol Eastmoney Guba list pages.
   - Current use: post title, author, clicks, replies, publish time when available.
   - Reference projects:
     - https://github.com/zcyeee/EastMoney_Crawler
     - https://github.com/algosenses/EastMoneySpider
     - https://github.com/Fucov/EastMoneyGuBaCrawler

2. `jiuyangongshe`
   - Scope: per-symbol search pages on Jiuyangongshe.
   - Current use: article link and snippet extraction from public SSR HTML.
   - Reference project:
     - https://github.com/jiaweif3ng/FinSpider

## Optional Cookie Or Browser Sources

These are useful but should not be enabled in the default production path without explicit opt-in.

1. `xueqiu`
   - Public `/S/{symbol}/POST` pages can return WAF-rendered HTML.
   - JSON comment/search APIs returned login or 403 errors in direct tests.
   - Best path: optional browser/cookie source, not default HTTP.
   - Reference projects:
     - https://github.com/xiaobeibei26/xueiqiu_spider
     - `references/UZI-Skill/skills/deep-analysis/scripts/lib/playwright_fallback.py`
     - `references/UZI-Skill/skills/deep-analysis/scripts/lib/xueqiu_browser.py`

2. `taoguba`
   - Public pages return HTML, but useful forum extraction generally needs cookie/session state.
   - Best path: optional cookie source with explicit local config.
   - Reference projects:
     - https://github.com/lisniuse/taoguba-crawler-skill
     - https://github.com/HRedL/taoguba
     - https://github.com/YuanjueDream/Taoguba_Data

3. `jisilu`
   - Public pages are reachable, but search output is not reliably stock-specific.
   - Best path: global community trend source or search-engine mediated fallback.

## Rejected For Default Path

- Xueqiu direct JSON APIs: require login/cookie or trigger 403.
- Taoguba direct forum crawling: cookie dependent and more fragile than Eastmoney/Jiuyangongshe.
- Selenium-only crawlers: useful as architecture references, but too heavy for the default skill runner.

## Implementation Rules

- Default providers must be no-key and no-cookie.
- Cookie/browser providers must be opt-in and must not store secrets in this skill.
- Per-provider failures must be recorded in `catalog.sqlite`; failures must not stop other providers.
- Use `data/normalized/forum/{symbol}.jsonl` for all forum/community normalized records.
