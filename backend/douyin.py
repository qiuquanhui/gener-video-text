"""抖音免 Cookie 解析通道。

背景：yt-dlp 的抖音提取器调用 web API 时需要浏览器 JS 生成的签名 Cookie
（s_v_web_id 等），上游 yt-dlp 尚无解法（源码留有 TODO）。本模块改走移动端
分享页匿名通道，全程不需要任何用户 Cookie：

1. HEAD https://www.douyin.com/ 拿服务端匿名下发的 ttwid（HttpOnly，非登录态）；
2. 携带 ttwid 请求 https://www.iesdouyin.com/share/video/{id}（移动端 UA），
   页面会 SSR 注入完整视频数据（window._ROUTER_DATA）；
3. play_addr 为 playwm（带水印）入口，路径替换为 /play/ 即无水印入口，
   两者均 302 至 CDN，可直接下载。

本模块全部为同步 stdlib 实现（无第三方依赖），由上层通过 asyncio.to_thread 调用。
"""
import http.client
import json
import re
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

VIDEO_ID_RE = re.compile(r"/video/(\d{15,25})")
ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)


class DouyinError(Exception):
    """抖音通道业务错误：message 面向用户。"""


# ---------- 链接识别与规范化 ----------

def is_douyin(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "douyin.com" or host.endswith(".douyin.com")


def normalize_douyin_url(url: str) -> str | None:
    """douyin.com 链接规范化（纯字符串操作，无网络 IO）。

    - ``/user/self?...&modal_id=N``（登录态个人主页弹窗链接，yt-dlp 不识别）→ /video/N
    - 其余 /video/N → 统一为 https://www.douyin.com/video/N
    非 douyin.com 链接返回 None；短链（v.douyin.com）原样返回，由 parse_info 内部解析。
    """
    if not is_douyin(url):
        return None
    m = re.search(r"/video/(\d+)", urlparse(url).path)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    modal = (parse_qs(urlparse(url).query).get("modal_id") or [""])[0]
    if modal.isdigit():
        return f"https://www.douyin.com/video/{modal}"
    return url


# ---------- 匿名 ttwid（服务端下发，带缓存） ----------

_ttwid_cache: tuple[str, float] | None = None


def _fetch_ttwid() -> str:
    for method in ("HEAD", "GET", "HEAD"):
        try:
            conn = http.client.HTTPSConnection("www.douyin.com", timeout=10)
            conn.request(method, "/", headers={"User-Agent": DESKTOP_UA})
            resp = conn.getresponse()
            cookie = resp.getheader("Set-Cookie") or ""
            conn.close()
            m = re.search(r"(?:^|,\s*)ttwid=([^;]+)", cookie)
            if m:
                return m.group(1)
        except OSError:
            pass
        time.sleep(1)
    raise DouyinError("无法从抖音获取匿名访问凭证，请稍后重试")


def _get_ttwid(force: bool = False) -> str:
    global _ttwid_cache
    if not force and _ttwid_cache and time.time() - _ttwid_cache[1] < 1200:
        return _ttwid_cache[0]
    ttwid = _fetch_ttwid()
    _ttwid_cache = (ttwid, time.time())
    return ttwid


# ---------- 分享页数据 ----------

def _resolve_video_id(url: str) -> str:
    """从任意抖音链接形态提取视频 ID；v.douyin.com 短链跟随跳转解析。"""
    m = VIDEO_ID_RE.search(url)
    if m:
        return m.group(1)
    modal = (parse_qs(urlparse(url).query).get("modal_id") or [""])[0]
    if modal.isdigit():
        return modal
    if (urlparse(url).hostname or "").startswith("v.douyin"):
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            final = resp.geturl()
        m2 = VIDEO_ID_RE.search(final) or re.search(r"modal_id=(\d+)", final)
        if m2:
            return m2.group(1)
    raise DouyinError("无法从链接中识别抖音视频，请使用视频页或 App 分享链接")


def _fetch_share_item(video_id: str) -> dict:
    """请求分享页返回 item_list[0]；失败时换新 ttwid 重试一次。"""
    for refresh in (False, True):
        ttwid = _get_ttwid(force=refresh)
        req = urllib.request.Request(
            f"https://www.iesdouyin.com/share/video/{video_id}",
            headers={"User-Agent": MOBILE_UA, "Cookie": f"ttwid={ttwid}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", "ignore")
        except OSError:
            continue
        m = ROUTER_DATA_RE.search(html)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            items = (data["loaderData"]["video_(id)/page"]["videoInfoRes"]
                     .get("item_list") or [])
            if items:
                return items[0]
        except (json.JSONDecodeError, KeyError):
            continue
    raise DouyinError("该视频无法通过抖音分享通道获取（可能已删除或需登录），请稍后重试")


def _play_urls(item: dict) -> tuple[str, str, int]:
    """返回 (无水印入口, 带水印入口, 清晰度高度)。"""
    v = item.get("video") or {}
    urls = (v.get("play_addr") or {}).get("url_list") or []
    if not urls:
        raise DouyinError("分享数据中没有可用的播放地址")
    wm = urls[0]
    nowm = wm.replace("/playwm/", "/play/")
    m = re.search(r"ratio=(\d+)p", wm)
    height = int(m.group(1)) if m else min(v.get("width") or 720, v.get("height") or 1280)
    return nowm, wm, height


def fetch_play_url(video_url: str) -> str:
    """下载时取新鲜的直链入口（无水印优先，失败回退带水印）。"""
    item = _fetch_share_item(_resolve_video_id(video_url))
    nowm, wm, _ = _play_urls(item)
    return nowm or wm


# ---------- 对外主入口 ----------

def parse_info(url: str) -> dict:
    """与 DownloadService.parse 的返回结构对齐。"""
    video_id = _resolve_video_id(url)
    item = _fetch_share_item(video_id)
    _, _, height = _play_urls(item)

    v = item.get("video") or {}
    cover = ((v.get("cover") or {}).get("url_list") or [""])[0] or None
    duration_ms = v.get("duration")
    return {
        "id": item.get("aweme_id") or video_id,
        "title": (item.get("desc") or "").strip() or "抖音视频",
        "thumbnail": cover,
        "duration": round(duration_ms / 1000, 1) if isinstance(duration_ms, (int, float)) else None,
        "uploader": (item.get("author") or {}).get("nickname") or "",
        "platform": "抖音",
        "webpage_url": f"https://www.douyin.com/video/{video_id}",
        "qualities": [
            {"value": str(height), "label": f"{height}P 高清", "type": "video",
             "filesize": None, "filesize_label": None},
            {"value": "mp3", "label": "MP3 音频", "type": "audio",
             "filesize": None, "filesize_label": None},
        ],
    }
