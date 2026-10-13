"""字幕文本提取：yt-dlp 下载平台字幕/自动字幕（srt/vtt）并转为纯文本。"""
import asyncio
import html
import re
import shutil
import uuid
from pathlib import Path

import config
import douyin
from downloader import YT_DLP, BusinessError, browser_args

TIMESTAMP_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}")
TAG_RE = re.compile(r"<[^>]+>")
VTT_META_PREFIXES = ("WEBVTT", "NOTE", "STYLE", "REGION", "Kind:", "Language:")


def parse_subtitle(path: Path) -> str:
    """srt/vtt -> 纯文本：去头部元数据/序号/时间轴/样式标签/HTML 实体，连续重复行去重。"""
    raw = path.read_text("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s or s.startswith(VTT_META_PREFIXES):
            continue
        if "-->" in s or TIMESTAMP_RE.fullmatch(s) or s.isdigit():
            continue
        s = html.unescape(TAG_RE.sub("", s)).replace("\u00a0", " ").strip()
        if not s:
            continue
        if out and out[-1] == s:  # 自动字幕滚动产生的连续重复
            continue
        out.append(s)
    return "\n".join(out).strip()


def _lang_score(name: str):
    n = name.lower()
    return ("ai-zh" in n or "zh-hans" in n or "zh-cn" in n, "zh" in n)


async def extract_text(url: str) -> dict:
    if douyin.is_douyin(url):
        raise BusinessError("抖音视频暂不支持字幕提取：分享通道不含字幕，AI 语音转写将在二期支持")
    workdir = config.DOWNLOAD_DIR / "subs" / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=True)
    args = [
        *YT_DLP, "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh.*,zh-Hans,zh,en",
        "--sub-format", "srt/vtt",
        "--no-playlist", "--no-warnings", "--socket-timeout", "15",
        "-o", str(workdir / "%(id)s.%(ext)s"),
        "--print", "after_move:title",
        *browser_args(url),
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
        title = lines[0] if lines else "字幕文本"

        sub_files = sorted(
            (f for f in workdir.iterdir() if f.suffix.lower() in (".srt", ".vtt")),
            key=lambda f: _lang_score(f.name), reverse=True,
        )
        if not sub_files:
            raise BusinessError("该视频没有可用字幕（平台未提供），AI 字幕转写功能将在二期支持")

        sub = sub_files[0]
        parts = sub.name.split(".")
        lang = parts[-2] if len(parts) >= 2 else "auto"
        text = parse_subtitle(sub)
        if not text:
            raise BusinessError("字幕文件内容为空")
        return {"title": title, "lang": lang, "chars": len(text), "text": text}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
