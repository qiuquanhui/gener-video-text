"""B 站匿名指纹（buvid3/buvid4）：获取、缓存与共享。

背景：B 站风控对缺少浏览器指纹的请求返回 HTTP 412（云服务器/机房 IP 尤其严格，
家宽出口通常放行）。匿名场景可先调官方指纹接口 finger/spi 换取 buvid3/buvid4——
它们是设备级匿名指纹，非登录态、不含任何个人身份——携带后公开 API 与
yt-dlp 均可正常访问。

失败退化：任何环境拿不到指纹时返回空，调用方回退原行为（家宽无 Cookie
本来就能用），零影响。
"""
import json
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
_TTL = 24 * 3600        # 指纹缓存有效期（匿名设备指纹，长有效期足够）
_FAIL_TTL = 60          # 获取失败时的短缓存，避免连续请求打爆指纹接口

_cache: tuple[list[tuple[str, str]], float] | None = None  # (cookie 键值对, 获取时间)


def _fetch() -> list[tuple[str, str]]:
    req = urllib.request.Request(SPI_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        doc = json.loads(resp.read().decode("utf-8", "ignore"))
    data = doc.get("data") or {}
    b3, b4 = str(data.get("b_3") or ""), str(data.get("b_4") or "")
    if not b3:
        return []
    return [("buvid3", b3)] + ([("buvid4", b4)] if b4 else [])


def cookie_pairs(force: bool = False) -> list[tuple[str, str]]:
    """返回指纹 Cookie 键值对（如 [("buvid3", ..), ("buvid4", ..)]）；失败返回空表。"""
    global _cache
    now = time.time()
    if not force and _cache:
        pairs, at = _cache
        if now - at < (_TTL if pairs else _FAIL_TTL):
            return pairs
    try:
        pairs = _fetch()
    except Exception:
        pairs = _cache[0] if _cache else []
    _cache = (pairs, now)
    return pairs


def cookie_header(force: bool = False) -> str:
    """直连请求用的 Cookie 头值（"buvid3=..; buvid4=.."）；失败返回空串。"""
    return "; ".join(f"{k}={v}" for k, v in cookie_pairs(force))
