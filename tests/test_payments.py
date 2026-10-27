"""Run: python -m unittest discover -s tests -p 'test_*.py'. No network calls.

验签走真实 stripe.Webhook.construct_event（测试内手工构造 Stripe 签名格式的
HMAC-SHA256 头），不 mock 密码学路径；Stripe 网络交互（建会话/查会话）才 mock。
"""
import hashlib
import hmac
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import auth_api
import config
import db
import main
import payments_api
import stripe_config
import stripe_gateway
from asgi_util import CookieJar, request

SECRET_KEY = "sk_test_51local"
WEBHOOK_SECRET = "whsec_test_local"


def stripe_sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """按 Stripe 规范构造 Stripe-Signature 头：t=时间戳,v1=HMAC-SHA256(t + '.' + payload)。"""
    t = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def build_event(event_id: str, order_id: str, payment_status: str = "paid") -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_" + event_id,
            "payment_status": payment_status,
            "metadata": {"order_id": order_id},
        }},
    }
    return json.dumps(event).encode()


class PaymentsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init_db(Path(self.tmp.name) / "test.db")
        auth_api._auth_hits.clear()
        # 固定 Stripe 配置（local_config.py / 环境变量不参与测试）
        settings = {"secret_key": SECRET_KEY, "webhook_secret": WEBHOOK_SECRET}
        patch.object(stripe_config, "get_stripe_settings", return_value=settings).start()
        patch.object(stripe_config, "stripe_enabled", return_value=True).start()
        self.jar = CookieJar()
        status, _, _ = await request(main.app, "POST", "/api/auth/register",
                                     {"email": "buyer@test.com", "password": "password123"},
                                     cookies=self.jar)
        self.assertEqual(status, 200)
        self.account = db.get_account_by_email("buyer@test.com")
        self.addCleanup(patch.stopall)

    async def create_order(self) -> dict:
        """走真实 HTTP 结账路径（mock 掉的只是 Stripe 网络调用）。"""
        fake = {"id": "cs_test_abc", "url": "https://checkout.stripe.com/c/pay/cs_test_abc"}
        with patch.object(stripe_gateway, "create_checkout_session", return_value=fake) as create:
            status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                            {"plan_id": "monthly"}, cookies=self.jar)
        self.assertEqual(status, 200)
        create.assert_called_once()
        # 金额/币种来自服务端 PLANS，与客户端输入无关
        plan_arg = create.call_args[0][0]
        self.assertEqual(plan_arg["amount"], config.PLANS["monthly"]["amount"])
        self.assertEqual(plan_arg["currency"], config.PLANS["monthly"]["currency"])
        return body

    async def deliver_webhook(self, payload: bytes, sig: str | None = None):
        headers = [(b"stripe-signature", (sig or stripe_sign(payload, WEBHOOK_SECRET)).encode())]
        return await request(main.app, "POST", "/api/webhooks/stripe", raw_body=payload,
                             cookies=CookieJar(), extra_headers=headers)

    async def test_plans_endpoint(self):
        status, body, _ = await request(main.app, "GET", "/api/vip/plans", cookies=CookieJar())
        self.assertEqual(status, 200)
        self.assertEqual(body["plans"][0]["id"], "monthly")
        self.assertEqual(body["plans"][0]["amount"], 990)

    async def test_checkout_requires_login(self):
        status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                        {"plan_id": "monthly"}, cookies=CookieJar())
        self.assertEqual(status, 401)

    async def test_checkout_creates_pending_order_with_server_side_price(self):
        body = await self.create_order()
        order = db.get_order(body["order_id"])
        self.assertEqual(order["status"], "pending")
        self.assertEqual(order["amount"], 990)
        self.assertEqual(order["currency"], "cny")
        self.assertEqual(order["stripe_session_id"], "cs_test_abc")

    async def test_checkout_reuses_pending_order_no_duplicate_session(self):
        first = await self.create_order()
        # 第二次结账：复用未支付订单，不新建 Stripe 会话（防开出两个收银台重复付款）
        reused_session = {"id": "cs_test_abc", "url": "https://checkout.stripe.com/c/pay/cs_test_abc",
                          "status": "open", "payment_status": "unpaid"}
        with patch.object(stripe_gateway, "retrieve_session", return_value=reused_session) as retrieve, \
                patch.object(stripe_gateway, "create_checkout_session") as create:
            status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                            {"plan_id": "monthly"}, cookies=self.jar)
        self.assertEqual(status, 200)
        self.assertEqual(body["order_id"], first["order_id"])
        self.assertEqual(body["checkout_url"], first["checkout_url"])
        create.assert_not_called()
        retrieve.assert_called_once()

    async def test_checkout_expired_session_gets_fresh_one_on_same_order(self):
        first = await self.create_order()
        expired = {"id": "cs_test_abc", "url": None, "status": "expired", "payment_status": "unpaid"}
        fresh = {"id": "cs_test_new", "url": "https://checkout.stripe.com/c/pay/cs_test_new"}
        with patch.object(stripe_gateway, "retrieve_session", return_value=expired), \
                patch.object(stripe_gateway, "create_checkout_session", return_value=fresh):
            status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                            {"plan_id": "monthly"}, cookies=self.jar)
        self.assertEqual(status, 200)
        self.assertEqual(body["order_id"], first["order_id"])          # 仍是同一订单
        self.assertEqual(body["checkout_url"], fresh["url"])
        self.assertEqual(db.get_order(first["order_id"])["stripe_session_id"], "cs_test_new")

    async def test_webhook_fulfills_order_and_grants_membership(self):
        body = await self.create_order()
        payload = build_event("evt_1", body["order_id"])
        status, resp, _ = await self.deliver_webhook(payload)
        self.assertEqual(status, 200)
        self.assertEqual(resp, {"received": True})
        self.assertEqual(db.get_order(body["order_id"])["status"], "paid")
        account = db.get_account(self.account["id"])
        self.assertIsNotNone(account["vip_expires_at"])
        self.assertGreater(account["vip_expires_at"], time.time())
        # 订单查询接口向付款人展示激活码（找回备用）
        status, order, _ = await request(main.app, "GET", f"/api/vip/order/{body['order_id']}",
                                         cookies=self.jar)
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "paid")
        self.assertIn("activation_code", order)
        self.assertTrue(order["account"]["is_vip"])

    async def test_duplicate_webhook_delivery_extends_only_once(self):
        """Stripe 官方明确会重复投递：同一事件两次，会员只能 +30 天而非 +60 天。"""
        body = await self.create_order()
        payload = build_event("evt_dup", body["order_id"])
        status, _, _ = await self.deliver_webhook(payload)
        self.assertEqual(status, 200)
        first_expiry = db.get_account(self.account["id"])["vip_expires_at"]
        status, _, _ = await self.deliver_webhook(payload)      # 完全相同的重投
        self.assertEqual(status, 200)
        second_expiry = db.get_account(self.account["id"])["vip_expires_at"]
        self.assertEqual(first_expiry, second_expiry)           # 未二次续期
        self.assertAlmostEqual(second_expiry - time.time(), 30 * 86400, delta=10)

    async def test_concurrent_fulfillment_of_two_paid_sessions_for_same_order(self):
        """极端场景：同一订单先后两个“已支付”事件（不同事件 ID）也只履约一次。"""
        body = await self.create_order()
        status, _, _ = await self.deliver_webhook(build_event("evt_a", body["order_id"]))
        self.assertEqual(status, 200)
        status, _, _ = await self.deliver_webhook(build_event("evt_b", body["order_id"]))
        self.assertEqual(status, 200)
        account = db.get_account(self.account["id"])
        self.assertAlmostEqual(account["vip_expires_at"] - time.time(), 30 * 86400, delta=10)

    async def test_webhook_bad_signature_rejected(self):
        body = await self.create_order()
        payload = build_event("evt_bad", body["order_id"])
        status, resp, _ = await self.deliver_webhook(payload, sig="t=1,v1=deadbeef")
        self.assertEqual(status, 400)
        self.assertEqual(db.get_order(body["order_id"])["status"], "pending")

    async def test_webhook_replayed_old_timestamp_rejected(self):
        """超过容忍窗口（默认 300 秒）的旧签名被拒（防重放）。"""
        body = await self.create_order()
        payload = build_event("evt_old", body["order_id"])
        status, _, _ = await self.deliver_webhook(payload, sig=stripe_sign(payload, WEBHOOK_SECRET,
                                                                          timestamp=int(time.time()) - 400))
        self.assertEqual(status, 400)

    async def test_webhook_wrong_secret_rejected(self):
        body = await self.create_order()
        payload = build_event("evt_wsec", body["order_id"])
        status, _, _ = await self.deliver_webhook(payload, sig=stripe_sign(payload, "whsec_other"))
        self.assertEqual(status, 400)

    async def test_webhook_unpaid_session_not_fulfilled(self):
        body = await self.create_order()
        payload = build_event("evt_unpaid", body["order_id"], payment_status="unpaid")
        status, _, _ = await self.deliver_webhook(payload)
        self.assertEqual(status, 200)
        self.assertEqual(db.get_order(body["order_id"])["status"], "pending")
        self.assertFalse(db.is_vip(db.get_account(self.account["id"])))

    async def test_webhook_unknown_order_returns_ok_without_granting(self):
        payload = build_event("evt_ghost", "order-does-not-exist")
        status, _, _ = await self.deliver_webhook(payload)
        self.assertEqual(status, 200)
        self.assertFalse(db.is_vip(db.get_account(self.account["id"])))

    async def test_order_polling_fulfills_when_webhook_lost(self):
        """webhook 丢失兜底：成功页轮询时服务端主动核实 Stripe 后幂等履约。"""
        body = await self.create_order()
        paid = {"id": "cs_test_abc", "url": None, "status": "complete", "payment_status": "paid"}
        with patch.object(stripe_gateway, "retrieve_session", return_value=paid):
            status, order, _ = await request(main.app, "GET",
                                             f"/api/vip/order/{body['order_id']}",
                                             cookies=self.jar)
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "paid")
        self.assertTrue(order["account"]["is_vip"])
        self.assertIn("activation_code", order)

    async def test_order_requires_owner(self):
        body = await self.create_order()
        other = CookieJar()
        status, _, _ = await request(main.app, "POST", "/api/auth/register",
                                     {"email": "other@test.com", "password": "password123"},
                                     cookies=other)
        self.assertEqual(status, 200)
        status, _, _ = await request(main.app, "GET", f"/api/vip/order/{body['order_id']}",
                                     cookies=other)
        self.assertEqual(status, 404)

    async def test_activate_platform_code_each_account_once(self):
        status, body, _ = await request(main.app, "POST", "/api/vip/activate",
                                        {"code": "vip2026"}, cookies=self.jar)
        self.assertEqual(status, 200)
        self.assertTrue(body["account"]["is_vip"])
        status, body, _ = await request(main.app, "POST", "/api/vip/activate",
                                        {"code": "VIP2026"}, cookies=self.jar)
        self.assertEqual(status, 400)
        self.assertIn("已兑换", body["detail"])

    async def test_activate_order_code_is_single_use_globally(self):
        body = await self.create_order()
        await self.deliver_webhook(build_event("evt_code", body["order_id"]))
        code = db.get_activation_code_by_order(body["order_id"])["code"]
        # 换一个账号兑换该订单码（赠送场景）：成功一次后全局失效
        other = CookieJar()
        await request(main.app, "POST", "/api/auth/register",
                      {"email": "friend@test.com", "password": "password123"}, cookies=other)
        status, _, _ = await request(main.app, "POST", "/api/vip/activate",
                                     {"code": code}, cookies=other)
        self.assertEqual(status, 200)
        third = CookieJar()
        await request(main.app, "POST", "/api/auth/register",
                      {"email": "third@test.com", "password": "password123"}, cookies=third)
        status, body, _ = await request(main.app, "POST", "/api/vip/activate",
                                        {"code": code}, cookies=third)
        self.assertEqual(status, 400)
        self.assertIn("已被使用", body["detail"])

    async def test_activate_invalid_code(self):
        status, body, _ = await request(main.app, "POST", "/api/vip/activate",
                                        {"code": "NOPE"}, cookies=self.jar)
        self.assertEqual(status, 400)
        self.assertIn("无效", body["detail"])

    async def test_stripe_unconfigured_returns_friendly_error(self):
        patch.object(stripe_config, "stripe_enabled", return_value=False).start()
        status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                        {"plan_id": "monthly"}, cookies=self.jar)
        self.assertEqual(status, 400)
        self.assertIn("STRIPE_SECRET_KEY", body["detail"])

    async def test_unknown_plan_rejected(self):
        with patch.object(stripe_gateway, "create_checkout_session"):
            status, body, _ = await request(main.app, "POST", "/api/vip/checkout",
                                            {"plan_id": "yearly"}, cookies=self.jar)
        self.assertEqual(status, 400)
        self.assertIn("套餐不存在", body["detail"])


if __name__ == "__main__":
    unittest.main()
