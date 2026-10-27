"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network calls.

B 站 API 直连通道（bilibili_video）单测：URL 解析/解析结构/档位回退/
durl 兜底/CDN 优选/ffmpeg 参数构建，全部离线 mock _api_get。
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import bilibili_video as bv
import downloader

VIEW = {
    "code": 0,
    "data": {
        "bvid": "BV1test00000", "aid": 123, "cid": 111, "duration": 100,
        "title": "测试视频", "pic": "https://pic/x.jpg",
        "owner": {"name": "测试UP"},
        "pages": [{"page": 1, "cid": 111}, {"page": 2, "cid": 222}],
    },
}
DASH = {
    "code": 0,
    "data": {
        "quality": 32,
        "dash": {
            "video": [
                {"id": 16, "height": 360, "bandwidth": 300000,
                 "baseUrl": "https://upos-src.bilivideo.com/v16.m4s",
                 "base_url": ["https://mcdn.bilivideo.cn:8082/v16b.m4s"]},
                {"id": 32, "height": 480, "bandwidth": 600000,
                 "baseUrl": "https://xy-mcdn.bilivideo.cn:8082/v32.m4s",
                 "base_url": ["https://upos-bak.bilivideo.com/v32b.m4s"]},
            ],
            "audio": [
                {"id": 30216, "bandwidth": 64000,
                 "baseUrl": "https://upos-src.bilivideo.com/a64.m4s"},
                {"id": 30280, "bandwidth": 320000,
                 "baseUrl": "https://upos-src.bilivideo.com/a320.m4s"},
            ],
        },
    },
}
DURL = {"code": 0, "data": {"quality": 32,
                            "durl": [{"url": "https://upos-x.bilivideo.com/v.mp4",
                                      "size": 123456}]}}


def fake_api(view=VIEW, play=DASH):
    def _get(url, referer):
        if "web-interface/view" in url:
            return view
        if "playurl" in url:
            return play
        raise AssertionError("unexpected url " + url)
    return _get


class ResolveTests(unittest.TestCase):
    def test_plain_and_p_param(self):
        self.assertEqual(bv._resolve_bvid("https://www.bilibili.com/video/BV1test00000/?x=1"),
                         ("BV1test00000", 1))
        self.assertEqual(bv._resolve_bvid("https://www.bilibili.com/video/BV1test00000?p=2"),
                         ("BV1test00000", 2))

    def test_b23_short_link_follows_redirect(self):
        class Resp:
            def geturl(self):
                return "https://www.bilibili.com/video/BV1test00000/?p=3"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        with patch.object(bv.urllib.request, "urlopen", return_value=Resp()):
            self.assertEqual(bv._resolve_bvid("https://b23.tv/abc123"),
                             ("BV1test00000", 3))

    def test_invalid_raises(self):
        with self.assertRaises(bv.BilibiliError):
            bv._resolve_bvid("https://www.bilibili.com/bangumi/xxx")


class ParseInfoTests(unittest.TestCase):
    def test_structure_and_qualities(self):
        with patch.object(bv, "_api_get", side_effect=fake_api()):
            info = bv.parse_info("https://www.bilibili.com/video/BV1test00000")
        self.assertEqual(info["title"], "测试视频")
        self.assertEqual(info["uploader"], "测试UP")
        self.assertEqual(info["platform"], "哔哩哔哩")
        self.assertEqual([q["value"] for q in info["qualities"]], ["32", "16", "mp3"])
        # filesize 由 bandwidth 估算：480P = 600000bps/8*100s ≈ 7.5MB
        self.assertAlmostEqual(info["qualities"][0]["filesize"], 7_500_000, delta=1)

    def test_part_page_cid(self):
        with patch.object(bv, "_api_get", side_effect=fake_api()) as m:
            bv.parse_info("https://www.bilibili.com/video/BV1test00000?p=2")
        play_urls = [c.args[0] for c in m.call_args_list if "playurl" in c.args[0]]
        self.assertTrue(play_urls, "未调用 playurl 接口")
        self.assertIn("cid=222", play_urls[0])  # 分 P 应使用对应页 cid

    def test_durl_fallback(self):
        with patch.object(bv, "_api_get", side_effect=fake_api(play=DURL)):
            info = bv.parse_info("https://www.bilibili.com/video/BV1test00000")
        self.assertEqual(info["qualities"][0]["value"], "32")
        self.assertEqual(info["qualities"][0]["filesize"], 123456)


class FetchPlayTests(unittest.TestCase):
    def test_upos_preferred(self):
        self.assertTrue(bv._candidate_urls(DASH["data"]["dash"]["video"][0])[0]
                        .startswith("https://upos"))

    def test_quality_match_and_fallback(self):
        with patch.object(bv, "_api_get", side_effect=fake_api()):
            play = bv.fetch_play("https://www.bilibili.com/video/BV1test00000", "16")
            self.assertEqual(play["kind"], "dash")
            self.assertIn("v16", play["video"][0])
            self.assertIn("a320", play["audio"][0])  # 最优音频
            play = bv.fetch_play("https://www.bilibili.com/video/BV1test00000", "80")
            self.assertIn("v32", play["video"][0])   # 80 不存在 → 回退最高档 32

    def test_mp3_uses_best_audio(self):
        with patch.object(bv, "_api_get", side_effect=fake_api()):
            play = bv.fetch_play("https://www.bilibili.com/video/BV1test00000", "mp3")
            self.assertFalse(play["video"])
            self.assertIn("a320", play["audio"][0])

    def test_durl(self):
        with patch.object(bv, "_api_get", side_effect=fake_api(play=DURL)):
            play = bv.fetch_play("https://www.bilibili.com/video/BV1test00000", "32")
            self.assertEqual(play["kind"], "durl")
            self.assertEqual(play["video"][0], "https://upos-x.bilivideo.com/v.mp4")


class FfmpegArgsTests(unittest.TestCase):
    def setUp(self):
        self.svc = downloader.DownloadService()

    def _task(self, quality):
        return {"id": "abcd", "quality": quality, "url": "https://www.bilibili.com/video/BV1test00000"}

    def test_dash_video_merge(self):
        args = self.svc._ffmpeg_args(self._task("32"), "/tmp/v.m4s", "/tmp/a.m4s")
        self.assertIn("-map", args)
        self.assertIn("aac_adtstoasc", args)
        self.assertEqual(args[-1].replace("\\", "/").split("/")[-1], "abcd.mp4")
        self.assertEqual(args.count("-i"), 2)

    def test_dash_without_audio(self):
        args = self.svc._ffmpeg_args(self._task("32"), "/tmp/v.m4s", None)
        self.assertEqual(args.count("-i"), 1)
        self.assertNotIn("-map", args)

    def test_mp3_transcode(self):
        args = self.svc._ffmpeg_args(self._task("mp3"), None, "/tmp/a.m4s")
        self.assertIn("libmp3lame", args)
        self.assertTrue(args[-1].endswith("abcd.mp3"))
        self.assertEqual(args.count("-i"), 1)

    def test_mp3_from_video_file(self):
        args = self.svc._ffmpeg_args(self._task("mp3"), "/tmp/v.mp4", None)
        self.assertIn("libmp3lame", args)


class ServiceDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_routes_bilibili_to_direct_channel(self):
        svc = downloader.DownloadService()
        expected = {"id": "BV1test00000", "qualities": []}
        with patch.object(bv, "parse_info", return_value=expected) as m:
            got = await svc.parse("https://www.bilibili.com/video/BV1test00000")
        self.assertEqual(got, expected)
        m.assert_called_once()

    async def test_download_task_marks_provider(self):
        svc = downloader.DownloadService()
        with patch.object(svc, "_download", new=AsyncMock()):  # 不真正下载
            task = await svc.start_download("https://www.bilibili.com/video/BV1test00000", "32", None)
            self.assertEqual(task["provider"], "bilibili")
            self.assertTrue(task["direct"])
            task2 = await svc.start_download("https://example.com/v", "720", None)
            self.assertIsNone(task2["provider"])
            self.assertFalse(task2["direct"])
            await asyncio.sleep(0.01)  # 让 _run 协程跑完，避免跨用例残留


if __name__ == "__main__":
    unittest.main()
