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
| 文本提取 | 一键提取视频字幕/自动字幕为纯文本，可复制/下载 .txt | ✅ |
| 全站免费 | 不限次数、不限清晰度、无需登录 | ✅ |
| AI 视频总结 | 会员专属（模拟支付），阶段 4 开发 | 🚧 |
| 会员/模拟支付 | 激活码 `VIP2026` 或 `DOWN8888` | 阶段 4 |

## 项目结构

```
├── docs/            # 需求文档 + 方案设计 + 项目总结（AI 扩展上下文）
├── backend/         # FastAPI + yt-dlp 子进程封装 + 抖音免 Cookie 分享流通道（douyin.py）
├── frontend/        # Vue3 单页（本地 vendor，零构建）
├── downloads/       # 临时文件（2 小时自动清理）
└── tests/fixtures/  # 字幕提取集成测试夹具（本地 HTML5 track 字幕页）
```

## API 概览

- `POST /api/parse` `{url}` → 视频信息 + 清晰度列表
- `POST /api/download` `{url, quality, title}` → `{task_id}`
- `GET /api/tasks/{id}` → 进度（状态/百分比/速度/ETA）
- `GET /api/file/{id}` → 文件下载
- `POST /api/extract-text` `{url}` → `{title, text, chars}`
- 完整契约见 `docs/方案设计.md`；交互式文档 `http://127.0.0.1:8000/api/docs`

## 已知限制（二期方向）

- 需登录内容（如 B 站 1080P+ 部分）匿名仅能获取较低清晰度；计划支持 Cookie 配置
- B 站字幕（含 AI 字幕）需登录才可获取，无字幕视频暂不能提取文本；计划接入 Whisper 本地转写
- 抖音走免 Cookie 移动端分享流（`backend/douyin.py`）：yt-dlp 官方抖音通道需浏览器签名 Cookie（上游无解），
  分享流可匿名获取**无水印 720P 单一清晰度** + MP3，暂不支持多清晰度；抖音无字幕接口，文本提取需等二期转写
- 小红书等少数平台暂不支持；YouTube 等境外平台需服务器网络可达
- LLM 未配置 Key 时，AI 总结将降级为本地抽取式摘要（阶段 4 实现说明见 docs/方案设计.md §7）

## 相关文档

| 文档 | 内容 |
|---|---|
| `docs/需求文档.md` | 背景目标、用户故事、功能清单与免费/付费边界 |
| `docs/方案设计.md` | 架构总览、API 契约、核心流程、UI 规范、扩展指引 |
| `docs/项目总结.md` | 交付复盘：需求/设计提炼、抖音免 Cookie 通道原理、踩坑记录、验证结论、二期路线 |

## 免责声明

仅供个人学习交流使用，请尊重内容创作者版权，勿用于任何侵权用途。
