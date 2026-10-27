"""Stripe 会员支付：套餐查询 / 结账 / 订单状态 / 激活码 / Webhook（阶段 5）。

安全与幂等设计（重点）：
- 金额只来自服务端 config.PLANS；客户端仅传 plan_id
- 每账号每套餐至多一个 pending 订单（DB 部分唯一索引），复用未过期会话或
  在同一订单上换发新会话——任一时刻只有一个可支付链接，防止开出两个收银台重复付款
- Webhook：construct_event 验签（防伪造与重放）→ webhook_events 表按事件 ID 去重
  （Stripe 会重复投递）→ mark_order_paid 条件更新保证 pending→paid 只发生一次，
  续期与发码在确认后执行；履约异常回滚事件记录并返回 500 让 Stripe 重试
- 成功页轮询 /api/vip/order/{id} 时若 webhook 尚未送达，服务端主动向 Stripe
  核实 payment_status 后走同一套幂等履约（双保险，成功页不卡死）
开闭原则：独立 APIRouter，main.py 仅追加 include_router，不改动既有路由。
"""
import asyncio
import logging
import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth_api
import config
import db
import stripe_config
import stripe_gateway
from auth_api import public_account
from downloader import BusinessError

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------- 套餐 ----------

@router.get("/api/vip/plans")
async def plans():
    return {"plans": list(config.PLANS.values())}


# ---------- 结账 ----------

class CheckoutRequest(BaseModel):
    plan_id: str


def _reuse_pending_checkout(account_id: int, plan: dict) -> dict | None:
    """存在未支付订单时复用其会话；会话已过期则在同一订单上换发新会话。"""
    pending = db.get_pending_order(account_id, plan["id"])
    if not pending:
        return None
    session = stripe_gateway.retrieve_session(pending["stripe_session_id"])
    if session and session.get("url"):
        return {"order_id": pending["id"], "checkout_url": session["url"]}
    # 旧会话已在 Stripe 侧过期（不可再支付）：同一订单换发新会话
    fresh = _create_session(pending["id"], account_id, plan)
    db.set_order_session(pending["id"], fresh["id"])
    return {"order_id": pending["id"], "checkout_url": fresh["url"]}


def _create_session(order_id: str, account_id: int, plan: dict) -> dict:
    base = config.PUBLIC_BASE_URL.rstrip("/")
    return stripe_gateway.create_checkout_session(
        plan, order_id, account_id,
        success_url=f"{base}/pay/success.html?order={order_id}",
        cancel_url=f"{base}/pay/cancel.html?order={order_id}",
    )


def _checkout(account: dict, plan_id: str) -> dict:
    if not stripe_config.stripe_enabled():
        raise BusinessError(
            "支付功能未配置：请在 backend/local_config.py 填写 STRIPE_SECRET_KEY"
            "（测试模式 sk_test_ 开头的密钥即可，见 docs/Stripe接入指南.md）"
        )
    plan = config.PLANS.get(plan_id)
    if not plan:
        raise BusinessError("套餐不存在")
    reused = _reuse_pending_checkout(account["id"], plan)
    if reused:
        return reused
    order_id = uuid.uuid4().hex
    try:
        db.create_order(order_id, account["id"], plan["id"], plan["amount"],
                        plan["currency"], stripe_session_id="")
    except sqlite3.IntegrityError:
        # 并发双击撞上“唯一 pending 订单”索引：复用对手方刚建的订单
        reused = _reuse_pending_checkout(account["id"], plan)
        if reused:
            return reused
        raise BusinessError("下单繁忙，请稍后重试")
    session = _create_session(order_id, account["id"], plan)
    db.set_order_session(order_id, session["id"])
    return {"order_id": order_id, "checkout_url": session["url"]}


@router.post("/api/vip/checkout")
async def checkout(req: CheckoutRequest, request: Request):
    account = auth_api.get_current_account(request)
    if not account:
        raise HTTPException(401, "请先登录后再购买会员")
    return await asyncio.to_thread(_checkout, account, req.plan_id)


# ---------- 履约（Webhook 与订单轮询共用的幂等入口） ----------

def _fulfill_order(order_id: str) -> bool:
    """pending→paid 确认成功才续期会员并生成激活码；重复调用无副作用。"""
    order = db.get_order(order_id)
    if not order:
        logger.warning("履约失败：订单不存在 %s", order_id)
        return False
    if order["status"] == "paid":
        return True
    plan = config.PLANS.get(order["plan_id"])
    if not plan:
        logger.error("履约失败：订单 %s 引用了不存在的套餐 %s", order_id, order["plan_id"])
        return False
    if not db.mark_order_paid(order_id):   # 条件更新：并发/重投递下仅首个成功
        return True                        # 已被并发方履约，视为成功
    db.extend_membership(order["account_id"], plan["days"])
    db.add_activation_code(db.new_activation_code(), plan["days"], order_id=order_id)
    logger.info("订单 %s 支付成功：账号 %s 会员 +%s 天", order_id, order["account_id"], plan["days"])
    return True


def _field(obj, key, default=None):
    """StripeObject 只支持字典式访问（属性访问会被拦截），统一安全取值。"""
    try:
        value = obj[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()          # 验签必须用原始请求体字节
    sig = request.headers.get("stripe-signature", "")
    settings = stripe_config.get_stripe_settings()
    if not settings["webhook_secret"] or not settings["secret_key"]:
        return JSONResponse(status_code=503, content={"detail": "Stripe 未配置"})
    try:
        event = stripe_gateway.construct_event(payload, sig, settings["webhook_secret"])
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Webhook 签名校验失败"})

    if not db.record_event(event["id"]):    # Stripe 会重复投递同一事件
        return {"received": True}

    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        if _field(session, "payment_status") == "paid":   # 延迟支付方式未到账不履约
            order_id = _field(_field(session, "metadata") or {}, "order_id")
            try:
                await asyncio.to_thread(_fulfill_order, order_id)
            except Exception:
                db.forget_event(event["id"])   # 让 Stripe 重试时能重新处理
                logger.exception("Webhook 履约失败：%s", order_id)
                return JSONResponse(status_code=500, content={"detail": "fulfillment failed"})
    return {"received": True}


# ---------- 订单查询（成功页轮询；兼作 webhook 丢失兜底） ----------

@router.get("/api/vip/order/{order_id}")
async def order_status(order_id: str, request: Request):
    account = auth_api.get_current_account(request)
    if not account:
        raise HTTPException(401, "请先登录")
    order = db.get_order(order_id)
    if not order or order["account_id"] != account["id"]:
        raise HTTPException(404, "订单不存在")
    if order["status"] == "pending" and stripe_config.stripe_enabled():
        # webhook 尚未送达：主动向 Stripe 核实后走同一套幂等履约
        session = await asyncio.to_thread(stripe_gateway.retrieve_session, order["stripe_session_id"])
        if session and session.get("payment_status") == "paid":
            await asyncio.to_thread(_fulfill_order, order_id)
            order = db.get_order(order_id)
    plan = config.PLANS.get(order["plan_id"], {})
    result = {
        "order_id": order["id"], "status": order["status"], "plan_id": order["plan_id"],
        "amount": order["amount"], "currency": order["currency"],
        "paid_at": order["paid_at"], "plan_name": plan.get("name", order["plan_id"]),
        "days": plan.get("days"), "account": public_account(db.get_account(account["id"])),
    }
    if order["status"] == "paid":
        code_row = db.get_activation_code_by_order(order_id)
        if code_row:
            result["activation_code"] = code_row["code"]   # 付款人可见，作找回备用
    return result


# ---------- 激活码兑换（落地 docs/方案设计.md 预留的 /api/vip/activate 契约） ----------

class ActivateRequest(BaseModel):
    code: str


@router.post("/api/vip/activate")
async def activate(req: ActivateRequest, request: Request):
    account = auth_api.get_current_account(request)
    if not account:
        raise HTTPException(401, "请先登录后再兑换会员")
    code = (req.code or "").strip().upper()
    if not code:
        raise BusinessError("请输入激活码")

    row = db.get_activation_code(code)
    if row:  # 订单生成的激活码：全局单次有效
        ok = db.redeem_code(code, account["id"], row["days"], single_use=True)
        if not ok:
            raise BusinessError("该激活码已被使用")
    elif code in config.VIP_CODES:  # 平台演示码：每账号限兑一次，送 30 天
        if not db.redeem_code(code, account["id"], 30, single_use=False):
            raise BusinessError("该激活码当前账号已兑换过")
    else:
        raise BusinessError("激活码无效")
    return {"account": public_account(db.get_account(account["id"]))}
