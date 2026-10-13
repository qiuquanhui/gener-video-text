"""yt-dlp 封装：视频解析、下载任务管理、进度解析、临时文件清理。

设计要点：
- 全部能力通过子进程调用 `python -m yt_dlp`（与运行环境一致，零修改开源代码）
- 任务仅存内存（无数据库）；文件落盘 downloads/，定期清理
- 进度解析依赖 `--progress --newline` 输出；最终文件路径来自 `--print after_move:filepath`
"""
import asyncio
import json
import re
import secrets
import shutil
import sys
import time
import uuid
from pathlib import Path

import config
import douyin

YT_DLP = [sys.executable, "-m", "yt_dlp"]
OUTPUT_TEMPLATE = str(config.DOWNLOAD_DIR / "%(id)s.%(ext)s")

# B 站风控缓解：浏览器 UA + Referer + 伪 buvid3 Cookie（匿名即可拉取播放地址）
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
FAKE_BUVID3 = "XY" + secrets.token_hex(16) + "infoc"


def browser_args(url: str) -> list:
    """针对 B 站域名附加防风控请求头；其他站点保持 yt-dlp 默认行为。"""
    if "bilibili.com" in url or "b23.tv" in url:
        return [
            "--user-agent", CHROME_UA,
            "--referer", "https://www.bilibili.com/",
            "--add-headers", f"Cookie: buvid3={FAKE_BUVID3}",
        ]
    return []

PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\w+)"
    r"(?:\s+at\s+((?:[\d.]+\w+/s)|Unknown\s*B/s))?"
    r"(?:\s+ETA\s+(?:Unknown|([\d:]+)))?"
)
FINAL_PATH_RE = re.compile(r"^[A-Za-z]:[\\/].+$|^/[^/].*$")
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".ogg", ".flv", ".mov", ".ts"}

PLATFORM_NAMES = {
    "BiliBili": "哔哩哔哩", "DouYin": "抖音", "YouTube": "YouTube",
    "Kuaishou": "快手", "Weibo": "微博", "TikTok": "TikTok",
    "Instagram": "Instagram", "Twitter": "X(Twitter)", "Youku": "优酷",
    "Tencent": "腾讯视频", "MangoTV": "芒果TV", "Ixigua": "西瓜视频",
}


class BusinessError(Exception):
    """业务错误：message 面向用户，由 API 层统一转为 4xx。"""


def sanitize_filename(name: str, fallback: str = "video") -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip(" ._")
    return name[:80] or fallback


def human_size(num) -> str | None:
    if not num:
        return None
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} PB"


def _quality_label(h: int) -> str:
    if h >= 2160:
        return "4K 超清"
    if h >= 1440:
        return "2K 超清"
    if h >= 1080:
        return "1080P 高清"
    if h >= 720:
        return "720P 高清"
    if h >= 480:
        return "480P 标清"
    return f"{h}P"


def _friendly_error(stderr_tail: str) -> str:
    lines = [
        l for l in (stderr_tail or "").strip().splitlines()
        if l.strip() and "Deprecated Feature" not in l and "potential security risk" not in l
    ]
    tail = "\n".join(lines)[-300:]
    low = tail.lower()
    if "unsupported url" in low:
        return "暂不支持该平台或链接无效，请检查链接是否正确（如小红书暂不支持）"
    if "login" in low or "cookies" in low or "sign in" in low:
        return "该内容需要登录后才能获取，暂不支持（Cookie 支持已列入二期计划）"
    if "private" in low:
        return "该内容为私有内容，无法下载"
    if "http error 404" in low or "not found" in low:
        return "视频不存在或已被删除"
    if "timed out" in low or "timeout" in low:
        return "网络超时，请稍后重试"
    return f"解析/下载失败：{tail or '未知错误'}"


class DownloadService:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

    # ---------- 解析 ----------

    async def parse(self, url: str) -> dict:
        # 抖音：yt-dlp 需要浏览器签名 Cookie（上游无解），走免 Cookie 分享页通道
        if douyin.is_douyin(url):
            try:
                return await asyncio.to_thread(douyin.parse_info, url)
            except douyin.DouyinError as e:
                raise BusinessError(str(e))
        proc = await asyncio.create_subprocess_exec(
            *YT_DLP, "-J", "--no-playlist", "--no-warnings",
            "--socket-timeout", "15", *browser_args(url), url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=config.PARSE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise BusinessError("解析超时，平台响应缓慢，请重试")
        if proc.returncode != 0:
            raise BusinessError(_friendly_error(err.decode("utf-8", "ignore")))
        try:
            info = json.loads(out.decode("utf-8", "ignore"))
        except json.JSONDecodeError:
            raise BusinessError("无法解析视频信息，请确认链接为视频页地址")
        return self._normalize_info(info)

    def _normalize_info(self, info: dict) -> dict:
        formats = info.get("formats") or []
        heights = sorted(
            {f.get("height") for f in formats
             if f.get("height") and f.get("vcodec") not in (None, "none")},
            reverse=True,
        )
        heights = [h for h in heights if h <= 2160][:6]

        qualities = []
        for h in heights:
            size = self._estimate_size(formats, h)
            qualities.append({
                "value": str(h),
                "label": _quality_label(h),
                "type": "video",
                "filesize": size,
                "filesize_label": human_size(size),
            })
        qualities.append({
            "value": "mp3", "label": "MP3 音频", "type": "audio",
            "filesize": None, "filesize_label": None,
        })

        platform = info.get("extractor_key") or ""
        return {
            "id": info.get("id"),
            "title": info.get("title") or "未命名视频",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel") or "",
            "platform": PLATFORM_NAMES.get(platform, platform or "未知平台"),
            "webpage_url": info.get("webpage_url") or info.get("original_url"),
            "qualities": qualities,
        }

    def _estimate_size(self, formats: list, height) -> int | None:
        same = [f for f in formats
                if f.get("height") == height and f.get("vcodec") not in (None, "none")]
        if not same:
            return None
        best_v = max(same, key=lambda f: f.get("tbr") or 0)
        size = best_v.get("filesize") or best_v.get("filesize_approx")
        if not size:
            return None
        audios = [f for f in formats
                  if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        if audios:
            ba = max(audios, key=lambda f: f.get("tbr") or 0)
            asize = ba.get("filesize") or ba.get("filesize_approx")
            if asize:
                size += asize
        return int(size)

    # ---------- 下载 ----------

    async def start_download(self, url: str, quality: str, title: str | None = None) -> dict:
        if len(self.tasks) >= config.MAX_TASKS:
            raise BusinessError("任务过多，请稍后再试")
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id, "url": url, "quality": quality, "title": title,
            "status": "queued", "progress": 0.0, "speed": None, "eta": None,
            "filename": None, "filepath": None, "error": None,
            "created_at": time.time(),
            "direct": douyin.is_douyin(url),  # 抖音分享流：直链媒体下载
        }
        self.tasks[task_id] = task
        asyncio.create_task(self._run(task))
        return task

    async def _run(self, task: dict):
        async with self._sem:
            try:
                await self._download(task)
            except Exception as e:  # 兜底：任何异常都不让协程静默死亡
                task["status"] = "error"
                task["error"] = str(e) if isinstance(e, BusinessError) else f"下载失败：{e}"

    def _build_args(self, task: dict) -> list:
        q = task["quality"]
        args = [
            *YT_DLP, "--no-playlist", "--no-warnings",
            "--progress", "--newline",
            "--print", "after_move:filepath",
            "--socket-timeout", "15",
        ]
        if task.get("direct"):
            # 抖音分享流：url 已是直链入口（aweme.snssdk.com 302 至 CDN），单流无格式选择；
            # 输出用 task_id 命名（直链 URL 中不含视频 ID，避免 %(id)s 撞名）
            args += [
                "--user-agent", douyin.MOBILE_UA,
                "-o", str(config.DOWNLOAD_DIR / f"{task['id']}.%(ext)s"),
            ]
            if q == "mp3":
                args += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            args += ["-o", OUTPUT_TEMPLATE]
            if q == "mp3":
                args += ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
            else:
                h = int(q)
                args += ["-f", f"bv*[height<={h}]+ba/b[height<={h}]", "--merge-output-format", "mp4"]
            args += browser_args(task["url"])
        args.append(task["url"])
        return args

    async def _download(self, task: dict):
        task["status"] = "downloading"
        # 视频（bv+ba 两段下载）按 2 段折算总进度；音频与直链按 1 段
        expected_frags = 1 if (task.get("direct") or task["quality"] == "mp3") else 2
        frag_index, last_pct = 1, 0.0

        if task.get("direct"):
            # 直链入口含时效签名，下载时现取新鲜的
            try:
                task["url"] = await asyncio.to_thread(douyin.fetch_play_url, task["url"])
            except douyin.DouyinError as e:
                task["status"] = "error"
                task["error"] = str(e)
                return

        proc = await asyncio.create_subprocess_exec(
            *self._build_args(task),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        final_paths: list[str] = []
        stderr_buf: list[str] = []

        async def _drain_stderr():
            try:
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    stderr_buf.append(line.decode("utf-8", "ignore"))
                    if len(stderr_buf) > 40:
                        stderr_buf.pop(0)
            except Exception:
                pass

        stderr_task = asyncio.create_task(_drain_stderr())

        while True:
            # 进度接近完成后的短暂等待即可标记"合并中/转码中"，否则用长超时防卡死
            short_wait = task["progress"] >= 99
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=3.0 if short_wait else config.LINE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                if short_wait:
                    merging = "converting" if task["quality"] == "mp3" else "merging"
                    if task["status"] != merging:
                        task["status"] = merging
                    continue
                proc.kill()
                raise BusinessError("下载长时间无响应，网络可能不稳定，请重试")
            if not raw:
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue

            m = PROGRESS_RE.match(line)
            if m:
                pct = float(m.group(1))
                if pct + 1.0 < last_pct:  # 进度回落 => 新分片开始
                    frag_index += 1
                last_pct = pct
                overall = (min(frag_index, expected_frags) - 1 + pct / 100) / expected_frags * 100
                task["progress"] = round(min(overall, 99.0), 1)
                task["speed"] = m.group(3)
                eta = m.group(4)
                task["eta"] = eta if eta and eta != "Unknown" else None
                continue
            if FINAL_PATH_RE.match(line) and Path(line).suffix.lower() in MEDIA_EXTS:
                final_paths.append(line)

        await proc.wait()
        stderr_task.cancel()

        if proc.returncode != 0:
            task["status"] = "error"
            task["error"] = _friendly_error("".join(stderr_buf))
            return

        filepath = next((p for p in reversed(final_paths) if Path(p).is_file()), None)
        if not filepath:
            task["status"] = "error"
            task["error"] = "下载完成但未找到输出文件，请重试或更换清晰度"
            return

        fp = Path(filepath)
        task.update(
            filepath=str(fp), filename=fp.name,
            status="done", progress=100.0, speed=None, eta=None,
        )

    # ---------- 任务视图 ----------

    def public_task(self, task_id: str) -> dict | None:
        t = self.tasks.get(task_id)
        if not t:
            return None
        view = {k: t.get(k) for k in ("status", "progress", "speed", "eta", "title", "filename", "error")}
        view["task_id"] = t["id"]
        if t["status"] == "done":
            view["download_url"] = f"/api/file/{t['id']}"
        return view

    def get_file_info(self, task_id: str):
        t = self.tasks.get(task_id)
        if not t or t["status"] != "done" or not t.get("filepath"):
            return None
        p = Path(t["filepath"])
        name = sanitize_filename(t.get("title") or p.stem) + p.suffix
        return p, name

    # ---------- 清理 ----------

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
            try:
                self.cleanup()
            except Exception:
                pass

    def cleanup(self):
        now = time.time()
        # 过期任务：删文件并置 expired / 移除已终结的失败任务
        for t in list(self.tasks.values()):
            if now - t["created_at"] > config.FILE_TTL_SECONDS:
                if t.get("filepath"):
                    try:
                        Path(t["filepath"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                if t["status"] == "done":
                    t["status"] = "expired"
                elif t["status"] in ("error", "expired"):
                    self.tasks.pop(t["id"], None)
        # 目录里的孤儿文件（失败任务残留等）
        for p in config.DOWNLOAD_DIR.iterdir():
            if p.is_file() and now - p.stat().st_mtime > config.FILE_TTL_SECONDS:
                try:
                    p.unlink()
                except OSError:
                    pass
        # 字幕提取临时目录（10 分钟兜底）
        subs = config.DOWNLOAD_DIR / "subs"
        if subs.is_dir():
            for d in subs.iterdir():
                if d.is_dir() and now - d.stat().st_mtime > 600:
                    shutil.rmtree(d, ignore_errors=True)
