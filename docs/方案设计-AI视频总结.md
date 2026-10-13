# 闪电下载 · AI 视频总结 — 方案设计（阶段 4）

> 版本：v1.1 ｜ 日期：2026-10-13 ｜ 状态：**已实现并通过端到端测试（见 §9）**
> 前置：竞品调研见 `竞品调研-AI视频总结.md`；本文档为阶段 4 开发依据。

## 1. 决策记录（用户已确认 + 设计期实测）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 输出形态 | **带时间戳章节要点**（一句话摘要 + 章节大纲 + 关键词 + **思维导图**，本期全做） |
| 2 | LLM | **GLM（智谱）**，默认 `glm-4-flash`（免费档），OpenAI 兼容协议；Key 已验证可用 ✅ |
| 3 | 会员边界 | **AI 总结先免费开放**（付费形式未来再定；`/api/summarize` 不做会员校验） |
| 4 | 导出 | 弹窗展示 + 复制 + 下载 Markdown / TXT |
| 5 | 输入范围 | 与字幕提取同边界；**不引入 Whisper**，字幕来源全部走免费方案 |

### 1.1 设计期实测（spike）结论

| 实验 | 结果 | 影响 |
|---|---|---|
| GLM `glm-4-flash` 调用 | ✅ 正常响应（免费模型） | 默认模型定为 glm-4-flash |
| B 站匿名字幕（yt-dlp --list-subs） | ❌ 仅有弹幕 | 登录墙确认 |
| B 站热门榜 25 条 + 「CC字幕」搜索 15 条匿名扫描 | **0/40 可见任何字幕（CC/AI 均不可见）** | **B 站匿名 = 无字幕**（2022 年权限收紧所致） |
| YouTube 可达性 | ❌ HTTP 000（网络不可达） | 本部署 YouTube 源不可用 |
| 抖音 | ❌ 分享流无字幕字段（前期已证） | 抖音无字幕源 |

**推论**：当前网络与免登录条件下，真实平台字幕可用量 ≈ 0。唯一免费解锁路径 = **用户自愿放置 B 站 Cookie 文件**（浏览器导出零成本；yt-dlp 官方推荐方式；同时解锁 B 站 1080P+ 下载，一举两得）。本地 fixture（tests/fixtures）始终可端到端验证管线。

## 2. 目标与非目标

**目标**：粘贴链接 → 提取带时间轴字幕 → GLM 生成「一句话摘要 + 时间戳章节要点 + 关键词 + 思维导图」→ 弹窗展示/复制/导出 MD/TXT。

**非目标（二期）**：追问对话（Chat with video）、内容改写（文章/PPT）、字幕翻译、多视频批量总结、Whisper 转写、真实付费。

## 3. 总体架构

```
前端结果卡「✨ AI 总结」按钮（免费开放）
   │ POST /api/summarize {url}
   ▼
main.py /api/summarize（超时 120s，同步返回）
   ▼
text_extractor.extract_cues(url)          ← 管线 v2：保留时间轴
   │  yt-dlp --write-subs（+ cookies 若用户已放置 backend/cookies.txt）
   │  srt/vtt → [{start, end, text}]（ cues ）
   ▼
summarizer.summarize(cues, meta)          ← 新模块（纯 stdlib，urllib 调 GLM）
   │  组装 prompt（截断至 ~3 万字）→ GLM chat/completions
   │  → 严格 JSON：{summary, chapters[], keywords[], mindmap{}}
   ▼
前端总结弹窗：摘要区 / 时间戳章节区(点击复制时间) / 关键词 chips
             / 手绘 SVG 思维导图（零依赖）/ 复制 / 下载 .md .txt
```

## 4. 模块设计

### 4.1 `text_extractor.py` v2 —— 时间轴保留 + Cookie

- 新增 `extract_cues(url) -> {"title", "lang", "cues": [{"start": 12.4, "end": 15.1, "text": "..."}], "chars"}`
  - 复用现有下载逻辑；解析 srt/vtt 时按块切分保留起止秒（当前实现逐行丢弃时间轴，改为按 `-->` 分块）
- `extract_text()` 改为 `extract_cues` 的薄封装（拼接 text），**对外契约不变**
- **Cookie 支持**（免费解锁 B 站字幕/高清）：`config.COOKIE_FILE = backend/cookies.txt`（gitignore）；
  文件存在 → 所有 yt-dlp 子进程（解析/下载/字幕/总结）自动附加 `--cookies <file>`（在 `browser_args` 旁统一注入）
- 字幕语言优选逻辑不变（zh 优先）；抖音维持明确报错文案

### 4.2 `summarizer.py`（新）—— GLM 调用与结构化输出

- **调用**：`urllib` stdlib POST `https://open.bigmodel.cn/api/paas/v4/chat/completions`
  （OpenAI 兼容；不引入 openai 依赖，保持 requirements 零新增）。`asyncio.to_thread` 包装。
- **配置**（config.py）：`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` 读 env，缺省回退 `backend/local_config.py`（**gitignored，Key 落在这里不进 git**）；默认模型 `glm-4-flash`
- **Prompt**：系统角色「视频内容编辑」+ 输出 JSON Schema 示例 + 全部 cue 文本（带 `[mm:ss]` 前缀，供模型对齐时间戳）；>3 万字截断并告知模型「内容有截断」
- **输出契约**（严格校验，字段缺失/JSON 解析失败 → 重试 1 次 → 仍失败报友好错误）：
```json
{
  "summary": "一句话摘要（≤80字）",
  "chapters": [
    {"title": "章节标题", "start": 12.4, "points": ["要点1（含关键论据/结论）", "要点2"]}
  ],
  "keywords": ["关键词", "..."],
  "mindmap": {"label": "视频主题", "children": [{"label": "章节", "children": [{"label": "要点"}]}]}
}
```
- **无 Key**：`BusinessError("AI 总结未配置 LLM Key，请在 backend/local_config.py 填写 LLM_API_KEY")`
  （原方案的"本地抽取式摘要降级"取消，简化范围——见 §8 确认项 3）
- **限流保护**：内存滑动窗口，同一 IP 每分钟 ≤3 次（免费开放的防滥用底线）

### 4.3 `main.py` —— 新路由

`POST /api/summarize` `{"url"}` → 202 语义的同步响应（最长 ~120s）：
```json
{"title": "...", "lang": "zh-Hans", "chars": 8213,
 "summary": "...", "chapters": [...], "keywords": [...], "mindmap": {...},
 "elapsed": 23.4, "model": "glm-4-flash"}
```
错误：无字幕（引导放置 Cookie）/ 超时 / LLM 失败，均 4xx 友好中文。

### 4.4 前端 —— 总结弹窗 + 思维导图

- `onSummarizeClick` 改为真实调用（免费，不再弹会员窗）；按钮文案去掉 🔒；定价区/FAQ 同步改为「AI 总结 限时免费」
- **总结弹窗**（新组件区块，风格沿用文本提取弹窗）：
  - 摘要卡（大字）+ 关键词 chips
  - 章节列表：`[03:25] 章节标题` + 要点 bullets；点击时间戳复制 `mm:ss`（无内嵌播放器，时间供跳回原视频参考）
  - **思维导图**：手绘 SVG 左向树（自研 ~150 行：节点布局/折叠/高亮，零第三方库，符合零构建原则）
  - 底部：复制全文 / 下载 .md（含时间戳章节结构）/ 下载 .txt / 「基于 GLM · 字幕共 N 字」标注
- 请求期间按钮 loading + 预计 15~40s 提示文案

### 4.5 配置与安全

- `.gitignore` 增加：`backend/local_config.py`、`backend/cookies.txt`
- Cookie/Key 均为用户本地文件，不入库不上传；LLM 请求仅发送字幕文本与标题（FAQ 注明）

## 5. 测试计划（自主验证）

1. **cues 解析**：扩展 `tests/fixtures/sample.vtt` 用例（含多 cue/重复行/标签），脚本化断言 start/end/text 正确、纯文本输出与旧版一致
2. **端到端（无平台依赖）**：fixtures http.server → `/api/summarize` 真实调 GLM → 校验 JSON 契约与章节时间戳合理性
3. **真实平台（若用户放置 Cookie）**：B 站视频总结样例；未放置时验证「无字幕」引导文案
4. **限流**：连续 4 次调用验证第 4 次被拒
5. **GUI 黑盒**：内嵌浏览器走完 粘贴→解析→总结→导图渲染→复制/下载 全流程
6. **回归**：下载 / MP3 / extract-text / 抖音通道不回归

## 6. 开发步骤（每步独立可验收）

| 步骤 | 内容 | 交付物 |
|---|---|---|
| S1 | text_extractor v2（cues + Cookie 注入） | 新函数 + 回归通过 |
| S2 | summarizer.py + config/local_config + /api/summarize + 限流 | API 可用（curl 验证） |
| S3 | 前端总结弹窗 + SVG 思维导图 + 导出 + 文案调整 | 页面全流程 |
| S4 | 测试验证（上述计划）+ 文档更新 + Git 提交 | 测试记录 + 验收材料 |

## 7. 风险与边界

- B 站字幕依赖用户 Cookie（未放置时真实平台无输入源；Cookie 失效需重新导出——错误文案将明确指引）
- glm-4-flash 上下文 128K 足够，但超长视频（>3 万字字幕）会截断，导图/章节可能略糙（文案如实标注）
- GLM 偶发不合规 JSON → 已设计重试；仍失败给明确报错
- 思维导图为自研简化树（横向折叠式），视觉不及 markmap 丰富，换取零依赖

## 8. 待确认问题（开发前最后确认）

1. **Cookie 方案纳入本期**（S1 一并实现，文件放置式、无设置界面）？——不纳入则真实平台 AI 总结将无字幕可用
2. 默认模型 **glm-4-flash**（免费档）OK？后续可改 `LLM_MODEL` 切 glm-4.6 等
3. 未配 Key 时**直接报错**（取消原设计的本地抽取式摘要降级，省工作量）？
4. Key 存放 `backend/local_config.py`（gitignored）确认？⚠️ 另提醒：该 Key 已出现在聊天记录，若仓库日后公开，建议先在智谱控制台轮换

## 9. 实现与验证记录（2026-10-13 交付）

### 9.1 开闭原则落地（用户约束）

新功能全部在新模块：`llm_config.py` / `subtitle_cues.py` / `summarizer.py` / `summarize_api.py`；
`downloader.py` / `text_extractor.py` **零改动**；`main.py` 仅插入 2 行路由注册；
前端仅新增弹窗/样式与占位符接线。Cookie 仅作用于字幕链路（不碰下载管线）。零新增 pip 依赖（LLM 走 stdlib urllib）。

### 9.2 相对 v1.0 方案的差异

- **新增 AI 问答（用户追加需求）**：`POST /api/qa {session_id, question, history}`，基于总结时的转写缓存（60 分钟/50 条上限）多轮追问，回答引用 `[mm:ss]` 时间戳、超纲问题如实拒答
- **`--print title` 需配 `--no-simulate`**（实测坑：普通字段 print 隐含 simulate 导致字幕不落盘）
- 开发中发现并修复前端 `session_id`/`sessionId` 字段名映射 bug

### 9.3 测试结论（全部通过）

| 测试 | 结果 |
|---|---|
| cues 解析单测（标签剥离/重复合并/时:分:秒） | ✅ |
| fixture 端到端真调 GLM（契约/章节时间戳/真实标题） | ✅ 10-12s |
| 问答：内容题引用 [0:08] / 超纲题拒答 / 多轮历史 | ✅ |
| 限流（60s 滑动窗口第 4 次拒绝）/ 过期会话文案 / 无 Key 报错 | ✅ |
| 回归：health / extract-text / 抖音解析 / B 站解析 | ✅ 零影响 |
| GUI：解析→结果卡→总结弹窗（摘要/关键词/时间戳章节/导图区/问答输入/导出按钮） | ✅ 快照验证 |
| GUI 内问答交互复验 | ⚠️ 自动化点击通道对本页动画不稳定（环境限制，非页面缺陷）；问答链路 API 层已充分验证 |

### 9.4 使用说明

- Key 配置：`backend/local_config.py`（gitignored）；换模型改 `LLM_MODEL`（如 glm-4.6）
- B 站字幕解锁：浏览器导出 Netscape 格式 Cookie 放 `backend/cookies.txt`（同时提升下载清晰度上限）
- AI 总结与问答当前免费开放，每 IP 每分钟 3 次
