# AI 视频总结 + AI 问答（阶段 4）实施计划

## 前置研究结论（已完成）
- **B 站字幕必须 Cookie**（yt-dlp 源码 + 社区文档 + 0/40 实测三方证据）→ Cookie 以**可选文件**支持（`backend/cookies.txt`，放置才生效，gitignored）
- **文档已核对**（Context7 + 实测）：GLM 用提示词驱动 JSON（官方最佳实践）；FastAPI 用 APIRouter + include_router

## 开闭原则落地方式（用户约束）
- 新功能全部放新模块，不改 `downloader.py`/`text_extractor.py` 任何现有逻辑
- 已有文件只做纯增量接线：`main.py` 插 2 行路由注册；前端占位按钮/占位文案接到真实逻辑
- Cookie 仅作用于新字幕链路，不碰下载管线；全程零新增 pip 依赖（LLM 用 urllib 调用）

## 新增/改动文件
| 文件 | 动作 | 内容 |
|---|---|---|
| `backend/llm_config.py` | 新增 | env 优先 → `backend/local_config.py`（gitignored）回退；GLM Key/模型/超时 |
| `backend/subtitle_cues.py` | 新增 | `extract_cues(url)`：字幕 → `[{start,end,text}]`；可选 `--cookies`；仅 import 复用 `browser_args`/`_lang_score` |
| `backend/summarizer.py` | 新增 | ① 总结：转写组装 `[mm:ss]`（3 万字截断）→ 严格 JSON prompt → 容错解析+重试 → `{summary, chapters[{title,start,points}], keywords, mindmap}`；② **AI 问答**：`answer_question(transcript, question, history)` 基于转写回答（引用 [mm:ss]，未涉及内容如实说明）；③ **转写会话缓存**：`{session_id → (转写文本, meta)}` 内存缓存（TTL 60 分钟，上限 50 条，访问时惰性过期），总结后返回 `session_id` 供问答复用，避免重复提取字幕；④ IP 限流 3 次/分钟（总结+问答共用） |
| `backend/summarize_api.py` | 新增 | APIRouter：`POST /api/summarize {url}`（免费开放）；`POST /api/qa {session_id, question, history[]}`（历史由前端携带，服务端无对话状态） |
| `backend/local_config.py` | 新增(gitignored) | GLM Key（绝不入 Git） |
| `.gitignore` | 追加 | `backend/local_config.py`、`backend/cookies.txt` |
| `backend/main.py` | +2 行 | import + include_router（置于静态托管前，纯增量） |
| `frontend/index.html` | 增量 | 「AI 总结弹窗」：摘要/关键词/时间戳章节/SVG 思维导图 + **AI 问答区**（输入框+发送+对话气泡，支持多轮）+ 复制/下载 MD/TXT；AI 按钮与定价占位文案改「免费」口径 |
| `frontend/js/app.js` | 增量 | doSummarize / askQuestion（含 loading 与历史管理）/ 自研 SVG 横向思维导图（~150 行可折叠）/ 导出；`onSummarizeClick` 占位接真 |
| `frontend/css/style.css` | 追加 | 总结弹窗、问答对话区、导图样式 |

## 行为契约
- `POST /api/summarize {"url"}` → `{session_id, title, lang, chars, summary, chapters, keywords, mindmap, elapsed, model}`
- `POST /api/qa {"session_id","question","history":[{"role","content"}]}` → `{answer, elapsed, model}`（会话过期 → 4xx 提示重新总结）
- 错误文案：无字幕（指引放置 Cookie）/ 未配 Key / 超时 / LLM 失败 / 限流，均友好中文 4xx；无 Key 直接报错（不做本地抽取式降级，已确认）

## 测试与验证（自主执行）
1. 模块导入/语法自检
2. 重启服务 → fixtures 本地 HTTP 服务 → `/api/summarize` 真实调 GLM：校验 JSON 契约、时间戳合理性
3. **问答多轮测试**：用返回的 session_id 连问 2-3 个问题（含一个视频中不存在的问题，验证"如实说明"），再测过期 session 报错
4. 限流：连续 4 次，第 4 次被拒
5. 回归：health / parse（B 站、抖音）/ extract-text（fixture）/ 下载链路不受影响
6. 浏览器 GUI 抽检：总结弹窗、思维导图、问答交互（尽力；通道不稳则 API 级验证并如实说明）

## 收尾
- 更新 `docs/方案设计-AI视频总结.md`（状态→已实现 + Cookie 结论 + OCP 说明 + 问答设计）与 README（功能表/已知限制）
- Git 提交（不含 Key/Cookie 文件）

## 已确认决策（不再询问）
glm-4-flash 默认可切换｜AI 总结+问答免费开放｜无 Key 直接报错｜Key 本地文件｜本期含思维导图、时间戳章节、AI 问答｜不引入 Whisper