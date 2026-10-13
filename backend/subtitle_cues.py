"""带时间轴字幕提取（AI 总结/问答的数据源）。

与 text_extractor.extract_text 的差异：保留每条字幕的起止时间（cues），
供 summarizer 生成「带时间戳章节要点」。开闭原则：本模块独立实现子进程
调用与 srt/vtt 解析，仅 import 复用既有模块的常量与工具，不修改任何现有代码。

可选 Cookie：存在 backend/cookies.txt（Netscape 格式，gitignored）时自动附加
--cookies。背景：B 站所有字幕（CC/AI）被平台服务端登录墙锁定（SESSDATA），
匿名不可得（yt-dlp 源码 need_login_subtitle 分支 + 0/40 实测，详见
docs/方案设计-AI视频总结.md §1.1）；用户放置自己浏览器导出的 Cookie 是唯一
免费解锁方式（亦使腾讯视频等匿名字幕平台更稳定）。
"""
import asyncio
import html
import re
import shutil
import uuid
from pathlib import Path

import config
import douyin
from downloader import YT_DLP, BusinessError, browser_args
from text_extractor import TAG_RE, _lang_score

COOKIE_FILE = config.BASE_DIR / "backend" / "cookies.txt"

CUE_TS_RE = re.compile(
    r"((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)


def cookie_args() -> list:
    """仅当用户放置了 cookies.txt 才启用；只作用于本字幕链路，不碰下载管线。"""
    if COOKIE_FILE.is_file():
        return ["--cookies", str(COOKIE_FILE)]
    return []


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

    workdir = config.DOWNLOAD_DIR / "subs" / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=True)
    args = [
        *YT_DLP, "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh.*,zh-Hans,zh,en",
        "--sub-format", "srt/vtt",
        "--no-playlist", "--no-warnings", "--socket-timeout", "15",
        "-o", str(workdir / "%(id)s.%(ext)s"),
        # after_move:title 在 --skip-download 下不输出；普通字段 print 会隐含
        # simulate 导致字幕不落盘，必须显式 --no-simulate
        "--print", "title", "--no-simulate",
        *browser_args(url), *cookie_args(),
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
        title = lines[0] if lines else "视频总结"

        sub_files = sorted(
            (f for f in workdir.iterdir() if f.suffix.lower() in (".srt", ".vtt")),
            key=lambda f: _lang_score(f.name), reverse=True,
        )
        if not sub_files:
            raise BusinessError(
                "该视频没有可用字幕（平台未提供或需要登录）。"
                "B 站所有字幕均需登录后可见：可将浏览器导出的 Cookie 放到 backend/cookies.txt 解锁"
            )

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
