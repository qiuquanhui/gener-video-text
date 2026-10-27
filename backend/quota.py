"""AI 免费额度：会员不限次；免费用户每日 3 次（登录按账号计，匿名按 IP 计）。

计费口径：每次实际发起 LLM 调用记 1 次——/api/summarize、/api/qa（每轮追问）、
/api/analyze（include_summary=True）、/api/analysis/{id}/summarize 与带总结的 retry；
纯文本提取、下载、解析不计费。
计费时机：在 HTTP 处理器入口预扣（AI 失败不退还，防重试刷量；与限流同思路）。
超限返回 402 Payment Required，前端据此唤起登录/开通会员弹窗。
"""
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request

import config
import db
from auth_api import get_current_account

router = APIRouter()


def _today() -> str:
    """本地日期（测试可 patch 此函数控制“跨天”）。"""
    return time.strftime("%Y-%m-%d")


def quota_subject(request: Request) -> tuple[dict | None, str, bool]:
    """返回 (账号或 None, 额度主体, 是否会员)。"""
    account = get_current_account(request)
    if account:
        return account, f"acct:{account['id']}", db.is_vip(account)
    host = request.client.host if request.client else "unknown"
    return None, f"ip:{host}", False


def quota_info(request: Request) -> dict:
    """当前额度状态（展示用），不扣减。"""
    account, subject, is_vip_flag = quota_subject(request)
    if is_vip_flag:
        return {"mode": "vip", "logged_in": True, "limit": None, "used": 0, "remaining": None}
    used = db.quota_used(subject, _today())
    remaining = max(config.FREE_AI_PER_DAY - used, 0)
    return {"mode": "account" if account else "anonymous",
            "logged_in": bool(account), "limit": config.FREE_AI_PER_DAY,
            "used": used, "remaining": remaining}


def consume_ai_quota(request: Request) -> dict:
    """预扣 1 次 AI 额度；会员直接放行，超限抛 402。"""
    account, subject, is_vip_flag = quota_subject(request)
    if is_vip_flag:
        return {"is_vip": True, "charged": False}
    used = db.quota_increment(subject, _today())
    if used > config.FREE_AI_PER_DAY:
        if account:
            message = (f"今日 {config.FREE_AI_PER_DAY} 次免费 AI 额度已用完，"
                       "开通会员即可不限次使用")
        else:
            message = (f"今日 {config.FREE_AI_PER_DAY} 次免费 AI 额度已用完："
                       "登录后继续使用，或开通会员不限次")
        raise HTTPException(status_code=402, detail=message)
    remaining = config.FREE_AI_PER_DAY - used
    return {"is_vip": False, "charged": True, "remaining": remaining}


@router.get("/api/quota")
async def get_quota(request: Request):
    return quota_info(request)
