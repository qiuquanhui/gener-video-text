"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network/LLM calls."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import analysis_api
import auth_api
import config
import db
import quota
from fastapi import HTTPException


class FakeRequest:
    """quota 只用到 cookies / client.host，直接构造轻量请求对象。"""

    def __init__(self, cookies=None, host="1.2.3.4"):
        self.cookies = cookies or {}
        self.client = type("Client", (), {"host": host})()


def fake_article(meta, notify):
    notify(ARTICLE)
    return ARTICLE


def session_cookie(token: str, account_id: int) -> dict:
    """按 auth_api 的存储方式（库存 SHA-256 摘要）直接造会话，返回 Cookie 字典。"""
    digest = hashlib.sha256(token.encode()).hexdigest()
    db.create_session(digest, account_id, 600)
    return {auth_api.COOKIE_NAME: token}


class QuotaUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init_db(Path(self.tmp.name) / "test.db")

    def test_anonymous_three_per_day_then_402(self):
        for i in range(config.FREE_AI_PER_DAY):
            result = quota.consume_ai_quota(FakeRequest(host="1.1.1.1"))
            self.assertFalse(result["is_vip"])
            self.assertEqual(result["remaining"], config.FREE_AI_PER_DAY - 1 - i)
        with self.assertRaises(HTTPException) as ctx:
            quota.consume_ai_quota(FakeRequest(host="1.1.1.1"))
        self.assertEqual(ctx.exception.status_code, 402)
        self.assertIn("登录", ctx.exception.detail)   # 匿名用户提示登录

    def test_quota_is_per_ip(self):
        for _ in range(config.FREE_AI_PER_DAY):
            quota.consume_ai_quota(FakeRequest(host="1.1.1.1"))
        # 不同 IP 互不影响
        result = quota.consume_ai_quota(FakeRequest(host="2.2.2.2"))
        self.assertEqual(result["remaining"], config.FREE_AI_PER_DAY - 1)

    def test_logged_in_quota_by_account_message_differs(self):
        aid = db.create_account("q@test.com", "h")
        req = FakeRequest(cookies=session_cookie("tok-quota", aid))
        for _ in range(config.FREE_AI_PER_DAY):
            quota.consume_ai_quota(req)
        with self.assertRaises(HTTPException) as ctx:
            quota.consume_ai_quota(req)
        self.assertEqual(ctx.exception.status_code, 402)
        self.assertIn("会员", ctx.exception.detail)   # 已登录用户提示开通会员

    def test_vip_unlimited(self):
        aid = db.create_account("vip@test.com", "h")
        db.extend_membership(aid, 30)
        req = FakeRequest(cookies=session_cookie("tok-vip", aid))
        for _ in range(config.FREE_AI_PER_DAY + 5):
            result = quota.consume_ai_quota(req)
            self.assertFalse(result["charged"])
        info = quota.quota_info(req)
        self.assertEqual(info["mode"], "vip")
        self.assertIsNone(info["limit"])

    def test_quota_resets_next_day(self):
        with patch.object(quota, "_today", return_value="2026-10-26"):
            for _ in range(config.FREE_AI_PER_DAY):
                quota.consume_ai_quota(FakeRequest(host="3.3.3.3"))
            with self.assertRaises(HTTPException):
                quota.consume_ai_quota(FakeRequest(host="3.3.3.3"))
        with patch.object(quota, "_today", return_value="2026-10-27"):
            result = quota.consume_ai_quota(FakeRequest(host="3.3.3.3"))
            self.assertEqual(result["remaining"], config.FREE_AI_PER_DAY - 1)

    def test_quota_info_modes(self):
        info = quota.quota_info(FakeRequest(host="4.4.4.4"))
        self.assertEqual(info["mode"], "anonymous")
        self.assertFalse(info["logged_in"])
        self.assertEqual(info["remaining"], config.FREE_AI_PER_DAY)


class QuotaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """HTTP 层验证额度接线：只 mock 掉 service（不产生后台任务，任务行为
    已由 test_analysis 覆盖），专注断言“计费发生在进入服务之前”。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init_db(Path(self.tmp.name) / "test.db")
        fake = MagicMock()
        fake.start.return_value = {"task_id": "t1", "status": "done"}
        self.fake_service = fake

    async def test_analyze_with_summary_consumes_quota_then_402(self):
        import main
        with patch.object(analysis_api, "service", self.fake_service):
            for _ in range(config.FREE_AI_PER_DAY):
                status, _, _ = await _asgi(main.app, "POST", "/api/analyze",
                                           {"url": "https://example.com/v", "include_summary": True})
                self.assertEqual(status, 200)
            self.assertEqual(self.fake_service.start.call_count, config.FREE_AI_PER_DAY)
            status, body, _ = await _asgi(main.app, "POST", "/api/analyze",
                                          {"url": "https://example.com/v", "include_summary": True})
            self.assertEqual(status, 402)
            self.assertIn("登录", body["detail"])
            self.assertEqual(self.fake_service.start.call_count, config.FREE_AI_PER_DAY)  # 未进入服务

    async def test_text_only_analyze_is_free_unlimited(self):
        import main
        with patch.object(analysis_api, "service", self.fake_service):
            for _ in range(config.FREE_AI_PER_DAY + 3):
                status, _, _ = await _asgi(main.app, "POST", "/api/analyze",
                                           {"url": "https://example.com/v"})
                self.assertEqual(status, 200)
            self.assertEqual(self.fake_service.start.call_count, config.FREE_AI_PER_DAY + 3)


async def _asgi(app, method, path, body=None):
    """直调 ASGI（等价 tests/asgi_util.request，避免循环依赖拷贝）。"""
    import json
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

    await app(scope, receive, send)
    raw = b"".join(m.get("body", b"") for m in messages)
    return messages[0]["status"], (json.loads(raw) if raw else {}), messages[0].get("headers", [])


if __name__ == "__main__":
    unittest.main()
