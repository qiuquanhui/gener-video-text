# SEO 优化说明

> 更新日期：2026-10-16 ｜ 依据《鱼厂 SEO 与 GEO 优化工作流》实施
> 本次为**纯增量改造**：只新增/增强 SEO 相关资产，未改动任何 Vue 业务逻辑与后端代码。

## 1. 本次改动清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `frontend/index.html` | 修改（仅 head + 新增 noscript） | TDK 按三段式规范重写；补全 Keywords / robots / canonical / OG / Twitter Card / itemprop / format-detection；新增 3 段 JSON-LD（WebSite、WebApplication、FAQPage）；`</div>` 后新增 `<noscript>` 兜底正文 |
| `frontend/robots.txt` | 新增 | 允许全站抓取、屏蔽 `/api/` 动态接口、声明 Sitemap |
| `frontend/sitemap.xml` | 新增 | 单页站点，首页一条记录（lastmod / changefreq / priority） |
| `frontend/404.html` | 新增 | 品牌风格 404 页（FastAPI `StaticFiles(html=True)` 对未知路径自动返回它 + 404 状态码） |
| `frontend/images/og-image.png` | 新增 | 1200×630 社交分享图（微信/QQ/微博分享卡片展示） |
| `scripts/generate_og_image.py` | 新增 | 分享图生成脚本（构建期工具，仅手动运行，**运行时零依赖**；用系统 Python 的 Pillow：`python scripts/generate_og_image.py`） |
| `docs/SEO 优化说明.md` | 新增 | 本文档 |

后端零改动：`StaticFiles` 已挂载在 `/`，上述静态文件自动可访问。

## 2. ⚠️ 上线前必做：替换占位域名

canonical / og:url / sitemap / robots 中使用了统一占位域名
`https://shandian-download.example.com`（`.example.com` 为保留域，绝不能带上线）。

确定真实域名后，在项目根目录执行全局替换（共 3 个文件、9 处）：

```bash
# Git Bash
grep -rln "shandian-download.example.com" frontend/   # 应列出 index.html / robots.txt / sitemap.xml
# 替换后自查：无残留占位符
grep -rn "example.com" frontend/ | grep -v "docs"
```

> 替换后建议重新生成分享图吗？不需要——图片内不包含域名。

## 3. TDK 设计依据（对照工作流）

### Title（三段式：页面名称 - 产品名称 | 核心介绍）

```
闪电下载 - 万能视频下载器 | 免费高清不限量 · 视频转文字 · AI 总结
```

- 主关键词「万能视频下载器」前置；核心词 22 个汉字，百度展示约 30 字以内不会被截断
- 覆盖：品牌词（闪电下载）+ 功能词（视频下载）+ 需求词（视频转文字、AI 总结）+ 利益词（免费高清不限量）

### Description（前 150 字符放核心信息，百度约 200 字符）

- 核心段（前 60 汉字）：产品定位 + 平台名（哔哩哔哩/抖音/YouTube/快手）+ 1800+ + 4K + 免费 + 无需登录
- 补充段：字幕转文字、AI 总结、行动号召「立即免费使用」

### Keywords（10 个，按重要性降序，含品牌词）

```
视频下载,万能视频下载器,免费视频下载,视频转文字,字幕提取,AI视频总结,哔哩哔哩视频下载,抖音视频下载,YouTube视频下载,闪电下载
```

Google 已不用 Keywords 排名，但百度 / 360 / 搜狗仍参考，保留无副作用。

## 4. 关键 SEO 决策说明

| 决策 | 原因 |
|---|---|
| FAQ 以 JSON-LD（FAQPage）+ noscript 双通道输出 | FAQ 手风琴内容原本只存在于 `app.js` 数据中，源码不可见；JSON-LD 供 Google 富摘要，noscript 供百度等不渲染 JS 的引擎 |
| 不把功能区/FAQ 改为静态 HTML 直出 | 遵循「不影响现有功能」约束，避免重构 Vue 模板引入回归风险；Google 能渲染 JS，百度由 noscript + JSON-LD 兜底 |
| 单 H1 原则保持 | Hero 大标题是页面唯一 H1（源码静态存在）；noscript 中只用 H2/H3，避免解析器看到双 H1 |
| robots 屏蔽 `/api/` | API 为动态 JSON（任务轮询、文件下载），抓取无价值且浪费爬虫预算 |
| 404 页 `noindex, follow` | 防止错误页被收录，同时保留继续抓取 |
| og:image 为绝对 URL + 1200×630 PNG | 工作流硬性要求；微信/QQ/微博抓取器不执行 JS，标签必须静态写在 HTML |

## 5. 上线检查清单（按工作流第七章）

**基础检查**
- [ ] 完成第 2 节占位域名替换
- [ ] `https://<域名>/robots.txt`、`/sitemap.xml`、`/images/og-image.png` 均 200
- [ ] 访问不存在路径返回 404 状态码 + 品牌页
- [ ] 启用 HTTPS（全站 + 无混合内容）

**提交收录**
- [ ] Google Search Console 添加资源 + 提交 sitemap
- [ ] 百度搜索资源平台添加站点 + 提交 sitemap（普通收录）
- [ ] 必应站长工具、360 站长平台、搜狗站长平台提交

**效果验证工具**
- [ ] Google Rich Results Test：验证 FAQPage / WebApplication 富摘要
- [ ] 微信开发者工具 / 手机实际转发：验证分享卡片图文
- [ ] PageSpeed Insights：Core Web Vitals（LCP<2.5s / INP<200ms / CLS<0.1）
- [ ] 百度移动友好度检测

**持续优化（每月）**
- [ ] GSC / 百度站长查看收录与搜索词，反哺 TDK 调整
- [ ] 后续若增加平台专属落地页（如 `/douyin-download` 长尾页），按工作流第二章为每类页面模板定制独立 TDK 并扩充 sitemap

## 6. GEO（AI 搜索）后续方向

✅ 已于 2026-10-20 实施，详见 **[GEO 优化说明.md](GEO 优化说明.md)**：llms.txt、robots.txt AI 爬虫显式授权、4 篇直接回答式教程落地页（`/guides/`）与目录页、sitemap 扩充。第 2 节的占位域名替换范围也随之扩大（9 个文件 56 处，以 grep 实际结果为准）。

## 7. 实测结论（2026-10-16 沉淀）

上线验证全部通过，数据留档：

**HTTP 端点（本地 127.0.0.1:8000 实测）**

| 端点 | 结果 |
|---|---|
| `/` | 200，SEO 标签命中 19 行（og:/twitter:/ld+json/canonical/keywords） |
| `/robots.txt` | 200，text/plain，含 Sitemap 声明 |
| `/sitemap.xml` | 200，text/xml |
| `/images/og-image.png` | 200，image/png，64,598 字节 |
| `/nonexistent-page` | **404 状态码** + 品牌 404 页（StaticFiles html 模式自动生效，无需后端改动） |
| `/api/health` | 200，不受影响 |

**结构化数据校验**：3 段 JSON-LD（WebSite / WebApplication / FAQPage）全部通过 `json.loads` 合法性解析；FAQPage 含 6 条完整问答；全页（含 noscript）仅 1 个 `h1`。

**功能回归（真实浏览器，Chrome 1280×720）**：Vue 正常挂载（`v-cloak` 已移除），8 张功能卡 / 6 条 FAQ / 10 个平台 chips 全部渲染；布局实测——导航 1265×65、hero 1265×402、功能网格 1080×439、定价 1080×519、FAQ 720×430、页脚 1265×115，页面总高 2651px，无 JS 报错。改动严格限定在 head + noscript，CSS 与 `app.js` 零改动，无视觉回归风险。

**OG 分享图**：视觉模型质检通过（无截断/重叠，胶囊、主标题、副标题、4 个权益胶囊完整清晰）；63KB 远小于 5MB 上限。

**环境备注**：IAB 内嵌浏览器截图捕获在当前环境不可用（guest 超时），以 DOM 计算布局校验替代视口截图，覆盖面等价（CSS 未变更）。

**遗留事项**：占位域名 `shandian-download.example.com` 待上线替换（第 2 节）；GEO 二期方向见第 6 节。
