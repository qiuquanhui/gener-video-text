"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network/LLM calls."""
import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import analysis_api
import main
import summarizer
from downloader import BusinessError

META = {"title": "测试视频", "lang": "zh", "chars": 8,
        "cues": [{"start": 0, "end": 2, "text": "先提取文本"}]}
SUMMARY = {"summary": "视频摘要", "chapters": [{"title": "第一章", "start": 0, "points": ["先提取文本"]}],
           "mindmap": {"label": "主题", "children": [{"label": "要点"}]}, "session_id": "session"}


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = analysis_api.AnalysisService()
        self.rate = patch.object(summarizer, "check_rate_limit").start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self):
        await self.service.shutdown()

    async def wait_status(self, task_id, status):
        async def wait():
            while self.service.public_task(task_id)["status"] != status:
                await asyncio.sleep(0.001)
            return self.service.public_task(task_id)
        return await asyncio.wait_for(wait(), 3)

    async def test_text_is_published_before_summary_and_extracted_once(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def summarize(meta):
            release.wait(2)
            return SUMMARY

        with patch.object(analysis_api, "extract_cues", AsyncMock(return_value=META)) as extract, \
                patch.object(summarizer, "summarize", side_effect=summarize) as summary:
            task = self.service.start("https://example.com/video", "test", include_summary=True)
            partial = await self.wait_status(task["task_id"], "summarizing")
            self.assertEqual(partial["text_result"]["text"], "先提取文本")
            self.assertEqual(partial["text_status"], "done")
            self.assertIsNone(partial["summary_result"])
            release.set()
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["summary_result"]["mindmap"], SUMMARY["mindmap"])
            extract.assert_awaited_once()
            summary.assert_called_once_with(META)
            self.assertNotIn("meta", result)
            self.assertNotIn("client_ip", result)

    async def test_summary_retry_preserves_text_without_reextracting(self):
        with patch.object(analysis_api, "extract_cues", AsyncMock(return_value=META)) as extract, \
                patch.object(summarizer, "summarize", side_effect=[BusinessError("AI 服务失败"), SUMMARY]) as summary:
            task = self.service.start("https://example.com/video", "test", include_summary=True)
            failed = await self.wait_status(task["task_id"], "error")
            self.assertEqual(failed["text_status"], "done")
            self.assertEqual(failed["summary_error"], "AI 服务失败")
            self.service.retry(task["task_id"], "test")
            self.service.retry(task["task_id"], "test")  # 重复点击不会追加工作。
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["text_result"], failed["text_result"])
            self.assertEqual(result["summary_error"], "")
            extract.assert_awaited_once()
            self.assertEqual(summary.call_count, 2)

    async def test_extraction_failure_can_retry(self):
        with patch.object(analysis_api, "extract_cues", AsyncMock(side_effect=[BusinessError("没有字幕"), META])), \
                patch.object(summarizer, "summarize", return_value=SUMMARY) as summary:
            task = self.service.start("https://example.com/video", "test", include_summary=True)
            failed = await self.wait_status(task["task_id"], "error")
            self.assertEqual(failed["text_error"], "没有字幕")
            summary.assert_not_called()
            self.service.retry(task["task_id"], "test")
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["text_error"], "")
            summary.assert_called_once()

    async def test_rate_limit_does_not_discard_text(self):
        self.rate.side_effect = BusinessError("请求太频繁")
        with patch.object(analysis_api, "extract_cues", AsyncMock(return_value=META)), \
                patch.object(summarizer, "summarize") as summary:
            task = self.service.start("https://example.com/video", "test", include_summary=True)
            result = await self.wait_status(task["task_id"], "error")
            self.assertEqual(result["text_status"], "done")
            self.assertEqual(result["summary_error"], "请求太频繁")
            summary.assert_not_called()

    async def test_default_only_extracts_text_and_ai_click_reuses_it(self):
        with patch.object(analysis_api, "extract_cues", AsyncMock(return_value=META)) as extract, \
                patch.object(summarizer, "summarize", return_value=SUMMARY) as summary:
            task = self.service.start("https://example.com/video", "test")
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["text_status"], "done")
            self.assertEqual(result["summary_status"], "idle")
            self.assertFalse(result["summary_requested"])
            self.assertIsNone(result["summary_result"])
            summary.assert_not_called()
            self.rate.assert_not_called()
            self.service.request_summary(task["task_id"], "test")
            self.service.request_summary(task["task_id"], "test")
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["summary_status"], "done")
            extract.assert_awaited_once()
            summary.assert_called_once_with(META)

    async def test_text_only_retry_never_calls_ai(self):
        with patch.object(analysis_api, "extract_cues", AsyncMock(side_effect=[BusinessError("没有字幕"), META])), \
                patch.object(summarizer, "summarize") as summary:
            task = self.service.start("https://example.com/video", "test")
            await self.wait_status(task["task_id"], "error")
            self.service.retry(task["task_id"], "test")
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["summary_status"], "idle")
            self.assertEqual(result["text_status"], "done")
            summary.assert_not_called()
            self.rate.assert_not_called()

    async def test_ai_request_during_extraction_does_not_duplicate_workers(self):
        ready = asyncio.Event()

        async def extract(url):
            await ready.wait()
            return META

        with patch.object(analysis_api, "extract_cues", AsyncMock(side_effect=extract)) as extractor, \
                patch.object(summarizer, "summarize", return_value=SUMMARY) as summary:
            task = self.service.start("https://example.com/video", "test")
            await self.wait_status(task["task_id"], "extracting")
            self.service.request_summary(task["task_id"], "test")
            self.service.request_summary(task["task_id"], "test")
            ready.set()
            result = await self.wait_status(task["task_id"], "done")
            self.assertEqual(result["summary_status"], "done")
            extractor.assert_awaited_once()
            summary.assert_called_once()

    async def test_expired_tasks_are_removed_but_active_tasks_survive(self):
        self.service.tasks = {
            "old": {"finished": time.time() - summarizer.SESSION_TTL_SECONDS - 1},
            "active": {"finished": None},
        }
        self.service.cleanup()
        self.assertEqual(list(self.service.tasks), ["active"])

    async def test_api_routes_validate_urls_and_report_missing_tasks(self):
        async def request(method, path, body=None):
            messages = []
            payload = json.dumps(body).encode() if body is not None else b""
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                     "query_string": b"", "headers": [(b"content-type", b"application/json")],
                     "client": ("test", 1), "server": ("test", 80)}

            async def receive():
                return {"type": "http.request", "body": payload, "more_body": False}

            async def send(message):
                messages.append(message)

            await main.app(scope, receive, send)
            return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages))

        with patch.object(analysis_api, "service", self.service), \
                patch.object(analysis_api, "extract_cues", AsyncMock(return_value=META)), \
                patch.object(summarizer, "summarize", return_value=SUMMARY):
            status, _ = await request("POST", "/api/analyze", {"url": "invalid"})
            self.assertEqual(status, 400)
            status, result = await request("POST", "/api/analyze", {"url": "https://example.com/video"})
            self.assertEqual(status, 200)
            await self.wait_status(result["task_id"], "done")
            self.assertEqual(self.service.public_task(result["task_id"])["summary_status"], "idle")
            status, result = await request("POST", "/api/analysis/" + result["task_id"] + "/summarize", {})
            self.assertEqual(status, 200)
            await self.wait_status(result["task_id"], "done")
            status, result = await request("GET", "/api/analysis/" + result["task_id"])
            self.assertEqual(status, 200)
            self.assertEqual(result["status"], "done")
            status, _ = await request("GET", "/api/analysis/missing")
            self.assertEqual(status, 404)
            status, _ = await request("POST", "/api/analysis/missing/retry", {})
            self.assertEqual(status, 404)
            status, _ = await request("POST", "/api/analysis/missing/summarize", {})
            self.assertEqual(status, 404)


class MindmapTests(unittest.TestCase):
    def test_missing_or_malformed_tree_uses_chapters(self):
        for mindmap in (None, {"label": "主题", "children": "invalid"}, {"label": "主题", "children": [None]}):
            data = {**SUMMARY, "mindmap": mindmap}
            result = summarizer._normalize_summary(data, "测试视频")
            self.assertEqual(result["mindmap"]["children"][0]["children"][0]["label"], "先提取文本")

    def test_tree_is_limited_to_three_levels(self):
        tree = {"label": "主题", "children": [{"label": "章节", "children": [{"label": "要点", "children": [{"label": "超出层级"}]}]}]}
        result = summarizer._normalize_summary({**SUMMARY, "mindmap": tree}, "视频")
        self.assertNotIn("children", result["mindmap"]["children"][0]["children"][0])


if __name__ == "__main__":
    unittest.main()
