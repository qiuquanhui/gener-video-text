"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network calls."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import auth_api
import db
import main
from asgi_util import CookieJar, request


class AuthTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init_db(Path(self.tmp.name) / "test.db")
        auth_api._auth_hits.clear()   # 限流是模块级状态，测试间重置
        self.jar = CookieJar()

    async def register(self, email="user@test.com", password="password123"):
        return await request(main.app, "POST", "/api/auth/register",
                             {"email": email, "password": password}, cookies=self.jar)

    async def test_register_login_me_logout_flow(self):
        status, body, _ = await self.register()
        self.assertEqual(status, 200)
        self.assertEqual(body["account"]["email"], "user@test.com")
        self.assertFalse(body["account"]["is_vip"])
        self.assertTrue(self.jar.cookies.get("sd_session"))

        status, body, _ = await request(main.app, "GET", "/api/auth/me", cookies=self.jar)
        self.assertTrue(body["logged_in"])

        # 登出后 me 恢复匿名
        status, _, _ = await request(main.app, "POST", "/api/auth/logout",
                                     {}, cookies=self.jar)
        self.assertEqual(status, 200)
        status, body, _ = await request(main.app, "GET", "/api/auth/me", cookies=self.jar)
        self.assertFalse(body["logged_in"])

        # 重新登录成功
        status, body, _ = await request(main.app, "POST", "/api/auth/login",
                                        {"email": "user@test.com", "password": "password123"},
                                        cookies=self.jar)
        self.assertEqual(status, 200)
        status, body, _ = await request(main.app, "GET", "/api/auth/me", cookies=self.jar)
        self.assertTrue(body["logged_in"])

    async def test_wrong_password_rejected(self):
        await self.register()
        self.jar = CookieJar()   # 换一个未登录会话
        status, body, _ = await request(main.app, "POST", "/api/auth/login",
                                        {"email": "user@test.com", "password": "wrong-password"},
                                        cookies=self.jar)
        self.assertEqual(status, 400)
        self.assertEqual(body["detail"], "邮箱或密码错误")

    async def test_duplicate_email_rejected(self):
        await self.register()
        self.jar = CookieJar()
        status, body, _ = await self.register()
        self.assertEqual(status, 400)
        self.assertIn("已注册", body["detail"])

    async def test_credentials_validation(self):
        status, body, _ = await self.register(email="not-an-email")
        self.assertEqual(status, 400)
        self.assertIn("邮箱格式", body["detail"])
        status, body, _ = await self.register(password="short")
        self.assertEqual(status, 400)
        self.assertIn("密码长度", body["detail"])

    async def test_expired_session_is_anonymous(self):
        await self.register()
        token = self.jar.cookies["sd_session"]
        import hashlib
        # 直接把会话改为已过期
        with db._conn() as conn:
            conn.execute("UPDATE sessions SET expires_at = 1 WHERE token_hash = ?",
                         (hashlib.sha256(token.encode()).hexdigest(),))
        status, body, _ = await request(main.app, "GET", "/api/auth/me", cookies=self.jar)
        self.assertFalse(body["logged_in"])

    async def test_login_rate_limited(self):
        for _ in range(auth_api.AUTH_RATE_LIMIT):
            await request(main.app, "POST", "/api/auth/login",
                          {"email": "nobody@test.com", "password": "whatever-long"},
                          cookies=CookieJar())
        status, body, _ = await request(main.app, "POST", "/api/auth/login",
                                        {"email": "nobody@test.com", "password": "whatever-long"},
                                        cookies=CookieJar())
        self.assertEqual(status, 400)
        self.assertIn("频繁", body["detail"])

    async def test_password_hashed_not_plaintext(self):
        await self.register(password="password123")
        account = db.get_account_by_email("user@test.com")
        self.assertNotIn("password123", account["password_hash"])
        self.assertTrue(account["password_hash"].startswith("scrypt$"))
        self.assertTrue(auth_api._verify_password("password123", account["password_hash"]))
        self.assertFalse(auth_api._verify_password("password124", account["password_hash"]))


if __name__ == "__main__":
    unittest.main()
