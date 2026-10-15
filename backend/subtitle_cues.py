"""带时间轴字幕提取（AI 总结/问答的数据源）。

与 text_extractor.extract_text 的差异：保留每条字幕的起止时间（cues），
供 summarizer 生成「带时间戳章节要点」。开闭原则：本模块独立实现子进程
调用与 srt/vtt 解析，仅 import 复用既有模块的常量与工具，不修改任何现有代码。

隐私：本链路不读取浏览器 Cookie，也不读取 backend/cookies.txt。字幕请求不发送
任何 Cookie；没有公开字幕时，可选本机 Whisper 从匿名音频流转写。
"""
import asyncio
import html
import os
import re
import shutil
import uuid
from pathlib import Path

import bilibili_subs
import config
import douyin
from downloader import YT_DLP, BusinessError, subtitle_browser_args
from local_transcriber import transcribe_url
from text_extractor import TAG_RE, _lang_score

CUE_TS_RE = re.compile(
    r"((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)


def _ts_to_seconds(ts: str) -> float:
    sec = 0.0
    for b in ts.replace(",", ".").split(":"):
        sec = sec * 60 + float(b)
    return sec


def parse_cues(path: Path) -> list[dict]:
    """srt/vtt → [{"start": 秒, "end": 秒, "text": "..."}]；去标签/实体/连续重复。"""
    raw = path.read_text("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for block in re.split(r"\n{2,}", raw):
        lines = [l for l in block.split("\n") if l.strip()]
        m = None
        body: list[str] = []
        for i, line in enumerate(lines):
            cm = CUE_TS_RE.search(line)
            if cm:
                m = cm
                body = lines[i + 1:]
                break
        if not m:
            continue
        text = " ".join(
            html.unescape(TAG_RE.sub("", l)).replace("\u00a0", " ").strip() for l in body
        ).strip()
        if not text:
            continue
        if cues and cues[-1]["text"] == text:  # 自动字幕滚动产生的连续重复
            cues[-1]["end"] = round(_ts_to_seconds(m.group(2)), 2)
            continue
        cues.append({
            "start": round(_ts_to_seconds(m.group(1)), 2),
            "end": round(_ts_to_seconds(m.group(2)), 2),
            "text": text,
        })
    return cues


async def extract_cues(url: str) -> dict:
    """下载字幕并返回带时间轴的数据：{"title","lang","chars","cues"}。"""
    if douyin.is_douyin(url):
        raise BusinessError("抖音视频暂不支持字幕提取：分享通道不含字幕，AI 语音转写将在二期支持")

    # B 站直连优先：两个轻量 HTTP 请求直取原生 JSON 字幕，免子进程；
    # 拿不到（无字幕轨 / 接口要求登录）自动回退下方 yt-dlp 路径。
    direct = await asyncio.to_thread(bilibili_subs.fetch_subtitle_cues, url)
    if direct:
        return {
            "title": direct["title"],
            "lang": direct["lang"],
            "chars": sum(len(c["text"]) for c in direct["cues"]),
            "cues": direct["cues"],
        }

    workdir = config.DOWNLOAD_DIR / "subs" / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=True)
    args = [
        *YT_DLP, "--ignore-config", "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh.*,zh-Hans,zh,en",
        "--sub-format", "srt/vtt",
        "--no-playlist", "--no-warnings", "--socket-timeout", "15",
        "-o", str(workdir / "%(id)s.%(ext)s"),
        # after_move:title 在 --skip-download 下不输出；普通字段 print 会隐含
        # simulate 导致字幕不落盘，必须显式 --no-simulate
        "--print", "title", "--no-simulate",
        *subtitle_browser_args(url),
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=config.EXTRACT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise BusinessError("字幕获取超时，请稍后重试")
        if proc.returncode != 0:
            from downloader import _friendly_error
            raise BusinessError(_friendly_error(err.decode("utf-8", "ignore")))

        lines = [l.strip() for l in out.decode("utf-8", "ignore").splitlines() if l.strip()]
        title = lines[0] if lines else "视频总结"

        sub_files = sorted(
            (f for f in workdir.iterdir() if f.suffix.lower() in (".srt", ".vtt")),
            key=lambda f: _lang_score(f.name), reverse=True,
        )
        if not sub_files:
            asr = await transcribe_url(url, workdir)
            cues = asr["cues"]
            return {
                "title": title,
                "lang": "zh-asr",
                "chars": len(asr["text"]),
                "cues": cues,
            }

        sub = sub_files[0]
        parts = sub.name.split(".")
        lang = parts[-2] if len(parts) >= 2 else "auto"
        cues = parse_cues(sub)
        if not cues:
            raise BusinessError("字幕文件内容为空，无法生成总结")
        chars = sum(len(c["text"]) for c in cues)
        return {"title": title, "lang": lang, "chars": chars, "cues": cues}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
