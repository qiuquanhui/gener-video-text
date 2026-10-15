"""B 站直连字幕通道（免 Cookie，纯 stdlib 同步实现，上层用 asyncio.to_thread 调）。

背景：yt-dlp 拉取 B 站字幕需启动子进程且对 AI 字幕（ai-zh）的成功率不稳定。
参考 liyupi/free-video-downloader 的做法，改用 B 站 web 端公开接口直取：

1. GET api.bilibili.com/x/web-interface/view?bvid=... → 标题 + aid/cid（支持分 P）；
2. GET api.bilibili.com/x/v2/dm/view?aid=...&oid=cid&type=1 → 字幕列表；
3. GET subtitle_url → B 站原生 JSON（body 数组自带 from/to 时间轴），无需解析 vtt。

隐私：与 yt-dlp 链路一致，不带任何 Cookie；AI 字幕若要求登录，匿名请求
拿到空列表即返回 None，由上层回退 yt-dlp / 本地语音转写，行为不变。
"""
import html
import json
import re
import urllib.request
from urllib.parse import parse_qs, urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
TAG_RE = re.compile(r"<[^>]+>")

# 语言优先级：中文人工字幕 > 中文自动字幕（ai-）> 其他中文 > 任意
def _sub_score(sub: dict) -> tuple:
    lan = str(sub.get("lan") or "").lower()
    if lan in ("zh-hans", "zh", "zh-cn"):
        return (0, 0)
    if lan.startswith("ai-zh") or "zh" in lan:
        return (1, len(lan))
    return (2, len(lan))


def is_bilibili(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _api_get(url: str, referer: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def fetch_subtitle_cues(url: str) -> dict | None:
    """B 站链接直取字幕；返回 {"title","lang","cues"}，拿不到（非 B 站 /
    接口失败 / 无字幕轨）返回 None，由上层回退 yt-dlp 路径。任何异常都不抛出。"""
    m = BV_RE.search(url)
    if not m:
        return None
    bvid = m.group(1)
    referer = f"https://www.bilibili.com/video/{bvid}"
    try:
        info = _api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", referer)
        if info.get("code") != 0:
            return None
        data = info.get("data") or {}
        title = str(data.get("title") or "").strip()
        cid, aid = data.get("cid"), data.get("aid")
        # 分 P 链接（?p=N）：取对应分 P 的 cid
        try:
            page = int((parse_qs(urlparse(url).query).get("p") or ["1"])[0])
        except ValueError:
            page = 1
        if page > 1:
            for pg in data.get("pages") or []:
                if pg.get("page") == page and pg.get("cid"):
                    cid = pg["cid"]
                    break
        if not cid:
            return None

        dm = _api_get(f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1", referer)
        if dm.get("code") != 0:
            return None
        subs = ((dm.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not subs:
            return None
        best = min(subs, key=_sub_score)

        sub_url = str(best.get("subtitle_url") or "")
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        elif sub_url.startswith("http://"):
            sub_url = "https://" + sub_url[7:]
        if not sub_url.startswith("https://"):
            return None

        doc = _api_get(sub_url, referer)
        cues: list[dict] = []
        for item in doc.get("body") or []:
            text = html.unescape(TAG_RE.sub("", str(item.get("content") or ""))).strip()
            if not text:
                continue
            start, end = round(float(item.get("from") or 0), 2), round(float(item.get("to") or 0), 2)
            if cues and cues[-1]["text"] == text:  # 滚动字幕连续重复，合并时段
                cues[-1]["end"] = end
                continue
            cues.append({"start": start, "end": end, "text": text})
        if not cues:
            return None
        return {"title": title or "B 站视频", "lang": str(best.get("lan") or "zh"), "cues": cues}
    except Exception:
        return None
