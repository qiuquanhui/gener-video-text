# ⚡ 闪电下载 — 万能视频下载网站

粘贴链接，一键下载全网视频（支持哔哩哔哩、抖音、YouTube 等 1800+ 平台）；免费、高清、不限次数；支持字幕文本提取，手机电脑全端可用。

> 基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 开源引擎封装（零修改），ffmpeg 负责音视频合并与 MP3 转码；抖音走免 Cookie 分享流独立通道（无水印下载，详见 `docs/项目总结.md`）。

## 快速开始

### 方式一：一键启动（Windows 推荐）

双击 `start.bat`（或在 cmd / PowerShell 中运行 `start.bat`）：

- 首次运行自动创建虚拟环境并安装依赖，之后直接启动
- 自动检测 ffmpeg（缺失仅警告：合并/MP3 转码将不可用）
- 端口已被占用时不重复启动，直接打开浏览器
- 启动约 3 秒后自动打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)

### 方式二：手动启动（Python 3.10+；本机需有 ffmpeg 在 PATH）

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe backend/main.py
# 访问 http://127.0.0.1:8000
```

## 功能

| 功能 | 说明 | 状态 |
|---|---|---|
| 视频解析 | 粘贴链接获取标题/封面/时长/清晰度（4K~360P、MP3） | ✅ |
| 视频下载 | 服务器下载合并 mp4 / 提取 MP3，实时进度（速度/ETA），文件保留 2 小时 | ✅ |
| 抖音免 Cookie | 个人主页弹窗链 / 视频链 / `v.douyin.com` 短链均可解析，**无水印 720P + MP3**，全程无需任何 Cookie | ✅ |
| 文本提取 | 一键提取视频字幕/自动字幕为纯文本，可复制/下载 .txt；B 站走免 Cookie 直连接口秒级取字幕（含平台 AI 字幕），拿不到时回退 yt-dlp，再回退本机 Whisper 转写 | ✅ |
| 分步内容分析 | 链接解析成功后只自动提取并展示文本；点击 AI 总结后复用文本生成总结和思维导图，失败可重试 | ✅ |
| 全站免费 | 不限次数、不限清晰度、无需登录 | ✅ |
| AI 视频总结 + AI 问答 | 已配置的 OpenAI 兼容模型生成摘要/时间戳章节要点/关键词/思维导图/**流式总结文章（SSE 逐段渲染，可复制/下载 .md）**，可多轮追问；导图支持全屏、拖拽平移缩放与 PNG 下载；LLM 调用自带瞬时故障重试与总时限兜底（当前免费开放，每分钟 3 次） | ✅ |
| 会员/模拟支付 | 激活码 `VIP2026` 或 `DOWN8888`（付费策略未来制定） | 🚧 |

## 项目结构

```
├── docs/            # 需求文档 + 方案设计 + 项目总结（AI 扩展上下文）
├── backend/         # FastAPI + yt-dlp 子进程封装 + 抖音免 Cookie 分享流通道（douyin.py）
├── frontend/        # Vue3 单页（本地 vendor，零构建）
├── downloads/       # 临时文件（2 小时自动清理）
└── tests/           # 后端与前端流程回归、字幕提取夹具
```

## API 概览

- `POST /api/parse` `{url}` → 视频信息 + 清晰度列表
- `POST /api/download` `{url, quality, title}` → `{task_id}`
- `GET /api/tasks/{id}` → 进度（状态/百分比/速度/ETA）
- `GET /api/file/{id}` → 文件下载
- `POST /api/extract-text` `{url}` → `{title, text, chars}`
- `POST /api/summarize` `{url}` → `{session_id, transcript, summary, chapters[{title,start,points}], keywords, mindmap, model}`（需配置 LLM Key）
- `POST /api/analyze` `{url, include_summary?: false}` → 默认仅提取文本；显式传 `true` 才同时生成 AI 总结和导图
- `POST /api/analysis/{id}/summarize` → 用户点击 AI 总结后，复用已有字幕时间轴生成总结和思维导图
- `GET /api/analysis/{id}` → 分阶段进度及 `text_result`、`summary_result`；文本提取成功后先展示，AI 失败时仍保留文本
- `POST /api/analysis/{id}/retry` → 按原任务模式重试；仅提取文本的任务不会调用 AI，已有文本的 AI 任务复用字幕时间轴（任务逻辑有效期 60 分钟，惰性清理）
- `POST /api/qa` `{session_id, question, history[]}` → `{answer}`（基于总结转写的多轮追问）
- 完整契约见 `docs/方案设计.md`；交互式文档 `http://127.0.0.1:8000/api/docs`

## 已知限制（二期方向）

- 需登录内容（如 B 站 1080P+ 部分）匿名仅能获取较低清晰度；计划支持 Cookie 配置
- 文本/总结链路不会读取浏览器 Cookie 或 `backend/cookies.txt`。B 站视频优先走免 Cookie 直连接口获取平台字幕（见 `backend/bilibili_subs.py`），失败回退 yt-dlp 公开字幕。没有公开字幕时，可选用本机 Whisper 从匿名音频流转写，音频不会上传；启用需安装 `openai-whisper`、PyTorch 和 FFmpeg，首次运行会下载模型权重到本机。无法匿名获取音频的视频仍不可提取
- 抖音走免 Cookie 移动端分享流（`backend/douyin.py`）：yt-dlp 官方抖音通道需浏览器签名 Cookie（上游无解），
  分享流可匿名获取**无水印 720P 单一清晰度** + MP3，暂不支持多清晰度；抖音无字幕接口，文本提取需等二期转写
- 小红书等少数平台暂不支持；YouTube 等境外平台需服务器网络可达
- AI 总结需通过环境变量或 `backend/local_config.py` 配置 LLM Key；使用 AI 时，字幕文本与视频标题会发送给所配置的 LLM 服务商。分析任务与问答会话保存在服务进程内存，逻辑有效期 60 分钟并惰性清理，重启后丢失
- 可通过 `ASR_PYTHON` 指定已安装 Whisper/PyTorch 的 Python，`ASR_MODEL` 选择 `base`、`small` 等模型，`ASR_MODEL_CACHE` 指定本机模型缓存目录；默认模型为 `base`

## 相关文档

| 文档 | 内容 |
|---|---|
| `docs/需求文档.md` | 背景目标、用户故事、功能清单与免费/付费边界 |
| `docs/方案设计.md` | 架构总览、API 契约、核心流程、UI 规范、扩展指引 |
| `docs/项目总结.md` | 交付复盘：需求/设计提炼、抖音免 Cookie 通道原理、踩坑记录、验证结论、二期路线 |
| [视频总结功能总结](docs/视频总结功能总结.md) | 视频总结最终需求、分步方案、架构、任务接口、配置、费用边界、15 项回归验证与已知限制 |

## 免责声明

仅供个人学习交流使用，请尊重内容创作者版权，勿用于任何侵权用途。
