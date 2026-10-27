"""B 站视频直连通道（纯 api.bilibili.com，绕开网页抓取）。

背景：B 站对云服务器/机房 IP 的**网页抓取**（yt-dlp 的第一步）执行严格风控，
带指纹 Cookie 也概率性 412；但 **web 公开 API**（view / playurl）在携带
buvid 指纹时稳定可用（与 bilibili_subs 字幕直连同一思路）。本通道与
douyin.py 的分享流通道同级：解析与下载均不经过 www.bilibili.com 页面。

链路（全部匿名 + 指纹 Cookie，无用户登录态）：
1. view API   → 标题/UP主/时长/封面/cid（支持 ?p=N 分 P）；
2. playurl API → DASH 流（视频 m4s + 音频 m4s，含备用 CDN）或老视频 durl mp4；
3. 下载由 downloader 用 ffmpeg 双输入流式合并（-c copy，边下边合）。

隐私：与字幕直连一致，只携带官方指纹接口换取的匿名设备指纹（bilibili_fp），
不读取/发送任何用户 Cookie。需登录的高清晰度（1080P+）匿名拿不到，属平台限制。
"""
import json
import re
import urllib.request
from urllib.parse import parse_qs, urlparse

import bilibili_fp
from bilibili_subs import BV_RE, UA, is_bilibili

QN_HEIGHT_LABELS = {16: "360P", 32: "480P", 64: "720P", 80: "1080P",
                    112: "1080P+", 116: "1080P60", 120: "4K", 125: "HDR"}


class BilibiliError(Exception):
    """B 站通道业务错误：message 面向用户。"""


def _api_get(url: str, referer: str) -> dict:
    headers = {"User-Agent": UA, "Referer": referer}
    cookie = bilibili_fp.cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _resolve_bvid(url: str) -> tuple[str, int]:
    """提取 (bvid, 分P号)；b23.tv 短链跟随跳转解析。失败抛 BilibiliError。"""
    if (urlparse(url).hostname or "").startswith("b23."):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                url = resp.geturl()
        except OSError as e:
            raise BilibiliError("短链跳转失败，请直接使用视频页链接") from e
    m = BV_RE.search(url)
    if not m:
        raise BilibiliError("无法识别 B 站视频，请使用视频页地址（含 BV 号）")
    try:
        page = int((parse_qs(urlparse(url).query).get("p") or ["1"])[0])
    except ValueError:
        page = 1
    return m.group(1), max(page, 1)


def _view_data(bvid: str, page: int) -> dict:
    info = _api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                    f"https://www.bilibili.com/video/{bvid}")
    if info.get("code") != 0:
        raise BilibiliError(f"视频信息获取失败（{info.get('message') or '接口异常'}）")
    data = info.get("data") or {}
    cid = data.get("cid")
    if page > 1:  # 分 P：取对应页的 cid
        for pg in data.get("pages") or []:
            if pg.get("page") == page and pg.get("cid"):
                cid = pg["cid"]
                break
    if not cid:
        raise BilibiliError("该视频缺少可用的分 P 信息")
    data["_cid"] = cid
    return data


def _playurl_data(bvid: str, cid: int) -> dict:
    r = _api_get(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        f"&qn=64&fnval=16&fourk=1",
        f"https://www.bilibili.com/video/{bvid}")
    if r.get("code") != 0:
        raise BilibiliError(f"播放地址获取失败（{r.get('message') or '接口异常'}）")
    return r.get("data") or {}


def _candidate_urls(stream: dict) -> list[str]:
    """流的可用 URL 列表：baseUrl + base_url 备用；常规 CDN（upos）优先。
    注：base_url 在部分响应里是字符串而非数组，需兼容。"""
    urls = [stream.get("baseUrl") or ""]
    base = stream.get("base_url") or []
    if isinstance(base, str):
        base = [base]
    urls += [u for u in base if u]
    urls = [u for u in urls if u]
    upos = [u for u in urls if "upos" in u]
    return (upos + [u for u in urls if u not in upos]) or urls


def parse_info(url: str) -> dict:
    """与 DownloadService.parse 的返回结构对齐。"""
    bvid, page = _resolve_bvid(url)
    view = _view_data(bvid, page)
    play = _playurl_data(bvid, int(view["_cid"]))
    duration = view.get("duration") or 0

    qualities = []
    for v in (play.get("dash") or {}).get("video") or []:
        qn, height = v.get("id"), v.get("height")
        if not qn or any(q["value"] == str(qn) for q in qualities):
            continue
        size = int(v.get("bandwidth") or 0) / 8 * duration or None
        label = QN_HEIGHT_LABELS.get(qn) or (f"{height}P 高清" if height else f"{qn}P")
        qualities.append({"value": str(qn), "label": label, "type": "video",
                          "filesize": size, "filesize_label": _human(size)})
    if not qualities:  # 老视频仅有 durl 单 mp4
        durl = play.get("durl") or []
        if durl:
            size = durl[0].get("size")
            qn = (play.get("quality") or 32)
            qualities.append({"value": str(qn), "label": QN_HEIGHT_LABELS.get(qn, "MP4 直连"),
                              "type": "video", "filesize": size, "filesize_label": _human(size)})
    if not qualities:
        raise BilibiliError("该视频没有匿名可用的播放地址（可能需要登录）")
    qualities.sort(key=lambda q: -int(q["value"]))
    qualities.append({"value": "mp3", "label": "MP3 音频", "type": "audio",
                      "filesize": None, "filesize_label": None})

    return {
        "id": bvid,
        "title": str(view.get("title") or "B 站视频").strip(),
        "thumbnail": view.get("pic"),
        "duration": duration,
        "uploader": (view.get("owner") or {}).get("name") or "",
        "platform": "哔哩哔哩",
        "webpage_url": f"https://www.bilibili.com/video/{bvid}" + (f"?p={page}" if page > 1 else ""),
        "qualities": qualities,
    }


def fetch_play(url: str, quality: str) -> dict:
    """下载时取新鲜直链（时效签名）。返回
    {"kind": "dash"|"durl", "duration": 秒, "video": [urls], "audio": [urls]}。"""
    bvid, page = _resolve_bvid(url)
    view = _view_data(bvid, page)
    play = _playurl_data(bvid, int(view["_cid"]))
    duration = view.get("duration") or 0
    dash = play.get("dash") or {}

    if quality == "mp3":
        audios = dash.get("audio") or []
        if not audios:
            raise BilibiliError("该视频没有独立的音频流，无法提取 MP3")
        best = max(audios, key=lambda a: a.get("bandwidth") or 0)
        return {"kind": "dash", "duration": duration, "video": [],
                "audio": _candidate_urls(best)}

    want = int(quality)
    videos = dash.get("video") or []
    match = [v for v in videos if v.get("id") == want]
    if not match and videos:  # 请求档位不可用：回退到最高可用档
        top = max(v.get("id") or 0 for v in videos)
        match = [v for v in videos if v.get("id") == top]
    if match:  # DASH：视频 + 最优音频，ffmpeg 合并
        best_v = max(match, key=lambda v: v.get("bandwidth") or 0)
        audios = dash.get("audio") or []
        best_a = max(audios, key=lambda a: a.get("bandwidth") or 0) if audios else None
        return {"kind": "dash", "duration": duration,
                "video": _candidate_urls(best_v),
                "audio": _candidate_urls(best_a) if best_a else []}

    durl = play.get("durl") or []
    if durl:  # 单 mp4 直链
        return {"kind": "durl", "duration": duration,
                "video": [u.get("url") or "" for u in durl if u.get("url")], "audio": []}
    raise BilibiliError("该视频没有匿名可用的播放地址（可能需要登录）")


def _human(num) -> str | None:
    if not num:
        return None
    num = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"
