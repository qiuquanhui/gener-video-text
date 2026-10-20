# GEO 优化说明

> 更新日期：2026-10-20 ｜ 承接《SEO 优化说明.md》第 6 节预留的 GEO 二期方向
> 目标：让 AI 对话助手（ChatGPT / Claude / Kimi / 豆包 / Perplexity / 文心等）在回答"怎么下载B站视频 / 抖音无水印 / 视频转文字 / AI 总结视频"等问题时，优先引用本站内容。
> 本次同样为**纯增量改造**：新增静态文件与教程页；现有页仅页脚加一个「使用指南」链接，Vue 逻辑与后端代码零改动。

## 1. 改动清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `frontend/llms.txt` | 新增 | 面向大模型的站点导览（llmstxt.org 规范）：H1 站点名 + 简介 + 指南/功能/须知分节，全部绝对链接 |
| `frontend/robots.txt` | 修改 | 在 `User-agent: *` 之外，显式欢迎 9 组 AI 爬虫（训练+检索），见第 3 节名单 |
| `frontend/guides/index.html` | 新增 | `/guides/` 使用指南目录页（ItemList 结构化数据） |
| `frontend/guides/bilibili-video-download.html` | 新增 | B站视频下载教程（约 1300 字） |
| `frontend/guides/douyin-no-watermark.html` | 新增 | 抖音无水印保存教程（约 1200 字） |
| `frontend/guides/video-to-text.html` | 新增 | 视频转文字教程（约 1250 字） |
| `frontend/guides/ai-video-summary.html` | 新增 | AI 视频总结教程（约 1300 字） |
| `frontend/sitemap.xml` | 修改 | 收录 6 个 URL（首页 + 指南目录 + 4 篇教程） |
| `frontend/index.html` | 修改（1 行 ×2 处） | 页脚与 noscript 版权行各加「使用指南」入口链接 |
| `docs/GEO 优化说明.md` | 新增 | 本文档 |

后端零改动：教程页为**自包含静态 HTML**（内联样式，不依赖 Vue 与 `/css/style.css`），由现有 `StaticFiles` 直接托管。

## 2. 教程页设计（GEO 核心）

**为什么是"直接回答式"静态长文**：AI 引擎回答用户提问时，偏好引用能直接回答问题、结构清晰（结论前置 / 步骤 / FAQ / 已知限制）、可抓取到完整正文的页面。AI 爬虫（GPTBot / ClaudeBot 等）抓取原始 HTML 且不执行 JS，纯静态页天然满足。

**每页统一结构**：首段 100 字内直接给答案（蓝底高亮块）→ 场景说明 → 编号步骤（对应 HowTo 结构化数据）→ 规格/策略表格 → FAQ 手风琴（对应 FAQPage）→ 已知限制（如实告知，来自需求文档 §6）→ CTA 返回首页 → 相关指南互链。

**页面模板 TDK 管理表**（按鱼厂工作流第二章要求的字段）：

| 页面 | 路径 | 标题模板 | 主关键词 | 负责人 | 更新时间 |
|---|---|---|---|---|---|
| 指南目录 | `/guides/` | 使用指南 - 闪电下载 \| 视频下载与 AI 总结教程大全 | 视频下载教程 | derder | 2026-10-20 |
| B站下载 | `/guides/bilibili-video-download.html` | B站视频怎么下载到本地 - 闪电下载 \| 手机电脑高清下载教程 | B站视频下载 | derder | 2026-10-20 |
| 抖音无水印 | `/guides/douyin-no-watermark.html` | 抖音视频怎么无水印保存 - 闪电下载 \| 三步下载教程 | 抖音无水印下载 | derder | 2026-10-20 |
| 视频转文字 | `/guides/video-to-text.html` | 视频怎么转文字 - 闪电下载 \| 字幕提取与语音转写教程 | 视频转文字 | derder | 2026-10-20 |
| AI 总结 | `/guides/ai-video-summary.html` | AI 视频总结怎么做 - 闪电下载 \| 摘要、章节、导图一键生成 | AI视频总结 | derder | 2026-10-20 |

每页 JSON-LD 三件套：`BreadcrumbList`（面包屑）+ `HowTo`（步骤，含 totalTime/tool/steps）+ `FAQPage`（3-4 条问答）；目录页为 `BreadcrumbList` + `ItemList`。全部通过 JSON 合法性校验。

**内容红线**：所有功能描述与限制均来自需求文档/README 的真实行为（抖音免 Cookie 720P 上限、文件保留 2 小时、AI 每分钟 3 次、转写音频不上传等），不虚构能力——AI 引擎与用户都会核验。

## 3. robots.txt AI 爬虫授权名单

| User-agent | 归属 | 用途 |
|---|---|---|
| GPTBot | OpenAI | 模型训练 |
| OAI-SearchBot | OpenAI | ChatGPT 搜索检索 |
| ChatGPT-User | OpenAI | 用户实时_fetch |
| ClaudeBot | Anthropic | 训练与检索 |
| Claude-User | Anthropic | 用户实时_fetch |
| PerplexityBot / Perplexity-User | Perplexity | 索引与用户_fetch |
| Bytespider | 字节跳动 | 豆包等 AI 产品 |
| CCBot | Common Crawl | 众多大模型的数据底料 |

均 `Allow: /` + `Disallow: /api/`（与通用规则一致）。百度/搜狗等国内蜘蛛由 `User-agent: *` 组覆盖，无需单列。

## 4. ⚠️ 上线前必做：占位域名替换

GEO 新增文件后，占位域名 `shandian-download.example.com` 现共出现于 **9 个文件、56 处**（index.html 8 / llms.txt 6 / sitemap.xml 7 / robots.txt 2 / guides 目录页 9 / 4 篇教程各 6）。替换与自查命令：

```bash
grep -rln "shandian-download.example.com" frontend/   # 应列出 9 个文件
# 替换完成后自查无残留：
grep -rn "example.com" frontend/ | grep -v robots.txt   # robots.txt 注释里的示例文字可保留
```

## 5. 上线与见效路径（GEO 特有）

GEO 没有类似 Search Console 的"提交收录"入口，AI 引擎通过自行抓取 + 搜索引擎索引获得内容，见效路径：

1. 完成域名替换 + HTTPS，确认 `/llms.txt`、`/guides/` 可公网访问；
2. sitemap 提交 Google / Bing / 百度（AI 检索普遍复用搜索引擎索引，Bing 对 ChatGPT 搜索尤其重要）；
3. 在知乎/掘金/公众号等平台发布教程并附带原文链接，外链是 AI 引擎发现与信任的重要信号；
4. 对话实测：在 ChatGPT / Kimi / 豆包 / Perplexity 提问"B站视频怎么下载"、"抖音无水印保存"，观察是否引用本站；
5. 每月复盘：根据实际引用情况补充新主题教程页（如 YouTube 下载、快手下载、字幕翻译），并更新 llms.txt 与 sitemap。

## 6. 实测结论（2026-10-20 沉淀）

上线验证全部通过，数据留档：

- **HTTP**：`/llms.txt` 200（text/plain）；`/guides/` 200；4 篇教程 `.html` 均 200；访问无扩展名 `/guides/xxx` 返回 404 + 品牌 404 页（当前 Starlette 版本无 `.html` 自动回退，因此站内链接、sitemap 与 llms.txt 一律直写 `.html` 全称，无跳转损耗）；`/` 200 且页脚与 noscript 各出现 1 处「使用指南」链接；`/robots.txt`、`/sitemap.xml` 更新后正常。
- **结构化数据**：5 个新页面共 14 段 JSON-LD 全部通过 JSON 合法性解析（BreadcrumbList×5、HowTo×4、FAQPage×4、目录页 ItemList×1）。
- **内容量**：4 篇教程正文（去标签）均 ≥1100 字，满足"内容有实质价值"的 GEO 底线。
- **功能回归**：首页 Vue 正常挂载，8 功能卡 / 6 FAQ / 10 平台 chips 渲染无异常，页脚新链接可见且指向 `/guides/`；`app.js`、`style.css`、后端零改动。

**遗留事项**：域名替换（第 4 节）；后续新主题教程页需同步更新 `llms.txt` 与 `sitemap.xml`（两处维护点已在本文件第 5 节流程中固化）。
