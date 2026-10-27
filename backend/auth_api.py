"""邮箱账号体系：注册 / 登录 / 登出 / 当前用户（阶段 5，Stripe 支付与 AI 额度的身份底座）。

产品原则：核心下载功能永久免登录；超过免费 AI 额度或购买会员时才要求登录。
安全设计：
- 密码 scrypt 加盐哈希（n=2^14，约 32MB 内存代价），库中不存明文；
  校验用 secrets.compare_digest 防时序侧信道
- 会话令牌 secrets.token_urlsafe(32)，库里只存 SHA-256 摘要；Cookie HttpOnly + SameSite=Lax
- 注册/登录限流：每 IP 每分钟 10 次（防撞库批量尝试）
开闭原则：独立 APIRouter，main.py 仅追加 include_router，不改动既有路由。
"""
import asyncio
import hashlib
import re
import secrets
import time
from collections import deque

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

import config
import db
from downloader import BusinessError

router = APIRouter()

COOKIE_NAME = "sd_session"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254
PASSWORD_MIN = 8
PASSWORD_MAX = 128

AUTH_RATE_LIMIT, AUTH_RATE_WINDOW = 10, 60  # 每 IP 每分钟次数
_auth_hits: dict[str, deque] = {}


def _check_auth_rate(client_ip: str) -> None:
    now = time.time()
    hits = _auth_hits.setdefault(client_ip, deque())
    while hits and now - hits[0] > AUTH_RATE_WINDOW:
        hits.popleft()
    if len(hits) >= AUTH_RATE_LIMIT:
        raise BusinessError("尝试过于频繁，请稍后再试")
    hits.append(now)


# ---------- 密码哈希（scrypt，stdlib） ----------

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=2 ** 14, r=8, p=1, dklen=32)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- 会话 ----------

def _issue_session(response: Response, account_id: int) -> None:
    token = secrets.token_urlsafe(32)
    db.create_session(_token_hash(token), account_id, config.SESSION_TTL_SECONDS)
    # secure=True 需 HTTPS 部署后开启（本地 http 下浏览器会拒绝该 Cookie）
    response.set_cookie(COOKIE_NAME, token, max_age=config.SESSION_TTL_SECONDS,
                        httponly=True, samesite="lax", path="/")


def get_current_account(request: Request) -> dict | None:
    """供额度 / 支付模块复用：当前登录账号（两次索引查询，<1ms，无需 to_thread）。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return db.get_session_account(_token_hash(token))


def public_account(account: dict) -> dict:
    return {
        "id": account["id"],
        "email": account["email"],
        "is_vip": db.is_vip(account),
        "vip_expires_at": account["vip_expires_at"],
    }


# ---------- 路由 ----------

class AuthRequest(BaseModel):
    email: str
    password: str


def _validate_credentials(email: str, password: str) -> tuple[str, str]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > EMAIL_MAX:
        raise BusinessError("邮箱格式不正确")
    if not (PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX):
        raise BusinessError(f"密码长度需在 {PASSWORD_MIN}-{PASSWORD_MAX} 位之间")
    return email, password


@router.post("/api/auth/register")
async def register(req: AuthRequest, request: Request, response: Response):
    _check_auth_rate(request.client.host if request.client else "unknown")
    email, password = _validate_credentials(req.email, req.password)
    if db.get_account_by_email(email):
        raise BusinessError("该邮箱已注册，请直接登录")
    account_id = await asyncio.to_thread(db.create_account, email, _hash_password(password))
    _issue_session(response, account_id)
    return {"account": public_account(db.get_account(account_id))}


@router.post("/api/auth/login")
async def login(req: AuthRequest, request: Request, response: Response):
    _check_auth_rate(request.client.host if request.client else "unknown")
    email = (req.email or "").strip().lower()
    account = db.get_account_by_email(email)
    stored = account["password_hash"] if account else "scrypt$00$00"
    # 无论账号是否存在都做同样的 scrypt 计算，避免响应时间差异暴露注册状态
    ok = await asyncio.to_thread(_verify_password, req.password or "", stored)
    if not account or not ok:
        raise BusinessError("邮箱或密码错误")
    _issue_session(response, account["id"])
    return {"account": public_account(db.get_account(account["id"]))}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_session(_token_hash(token))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
async def me(request: Request):
    account = get_current_account(request)
    if not account:
        return {"logged_in": False}
    return {"logged_in": True, "account": public_account(account)}
