"""Stripe SDK 薄封装：全部 Stripe 网络交互集中在此，便于单元测试整体 mock。

为什么引入官方 SDK（本项目首个第三方 API 依赖）：Webhook 验签是
HMAC-SHA256 + 时间戳容忍窗口（默认 300 秒防重放）的安全关键逻辑，
必须使用官方 construct_event 实现，不手搓密码学。
API 依据：docs.stripe.com/payments/checkout（Checkout Session 创建）与
docs.stripe.com/webhooks（验签与幂等处理）。
"""
import logging

import stripe as stripe_sdk

import stripe_config

logger = logging.getLogger(__name__)


def _apply_key() -> None:
    # 每次调用前设置密钥：local_config.py 修改后无需重启即生效（与 llm_config 模式一致）
    stripe_sdk.api_key = stripe_config.get_stripe_settings()["secret_key"]


def create_checkout_session(plan: dict, order_id: str, account_id: int,
                            success_url: str, cancel_url: str) -> dict:
    """创建一次性买断（mode=payment）的托管收银台会话。

    金额与币种只来自服务端 config.PLANS，客户端仅能传 plan_id（防篡改金额）。
    """
    _apply_key()
    session = stripe_sdk.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "product_data": {"name": plan["name"]},
                "currency": plan["currency"],    # Stripe 要求小写币种代码，如 cny
                "unit_amount": plan["amount"],   # 最小货币单位（CNY 即“分”）
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(account_id),
        metadata={"order_id": order_id, "account_id": str(account_id)},
    )
    return {"id": session.id, "url": session.url}


def retrieve_session(session_id: str) -> dict | None:
    """取会话当前状态（订单轮询的 webhook 丢失兜底）；网络/对象不存在返回 None。"""
    _apply_key()
    try:
        s = stripe_sdk.checkout.Session.retrieve(session_id)
    except stripe_sdk.StripeError:
        logger.warning("Stripe 会话查询失败：%s", session_id, exc_info=True)
        return None
    return {
        "id": s.id,
        "url": getattr(s, "url", None),          # 会话过期后 url 为空
        "status": s.status,                      # open | complete | expired
        "payment_status": s.payment_status,      # paid | unpaid
    }


def construct_event(payload: bytes, sig_header: str, secret: str) -> dict:
    """Webhook 验签：签名不符 / 时间戳超窗抛异常，由调用方转 400。"""
    return stripe_sdk.Webhook.construct_event(payload, sig_header, secret)
