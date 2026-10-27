"""SQLite 持久层：账号 / 会话 / 订单 / Webhook 去重 / AI 额度 / 激活码。

真实支付要求状态可持久（重启不丢会员），故引入 SQLite（stdlib，零新依赖）。
连接策略：每个操作独立短连接 + WAL + busy_timeout；上层用 asyncio.to_thread 调用。
测试通过替换模块级 DB_PATH 指向临时文件来隔离。
"""
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import config

DATA_DIR = config.BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    vip_expires_at REAL,
    created_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id),
    plan_id           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending | paid
    stripe_session_id TEXT UNIQUE,
    amount            INTEGER NOT NULL,                 -- 单位：最小货币单位（分）
    currency          TEXT NOT NULL,
    created_at        REAL NOT NULL,
    paid_at           REAL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id    TEXT PRIMARY KEY,
    received_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_usage (
    subject TEXT NOT NULL,                              -- "ip:1.2.3.4" 或 "acct:42"
    day     TEXT NOT NULL,                              -- 本地日期 "2026-10-26"
    used    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, day)
);
CREATE TABLE IF NOT EXISTS activation_codes (
    code       TEXT PRIMARY KEY,
    days       INTEGER NOT NULL,
    order_id   TEXT UNIQUE,                             -- 订单生成的码绑定订单；平台演示码为 NULL
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS code_redemptions (
    code        TEXT NOT NULL,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    redeemed_at REAL NOT NULL,
    PRIMARY KEY (code, account_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, status);
-- 任一时刻每账号每套餐至多一个 pending 订单（数据库层防并发重复下单/重复付款）
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_unique_pending
    ON orders(account_id, plan_id) WHERE status = 'pending';
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def _conn():
    """每个操作一个短连接：成功提交、异常回滚，无论哪种都关闭连接。"""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    """建库建表（幂等）。WAL 是库级持久设置，初始化时设置一次。"""
    global DB_PATH
    if path is not None:
        DB_PATH = Path(path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


init_db()


# ---------- 账号与会话 ----------

def create_account(email: str, password_hash: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, password_hash, time.time()),
        )
        return cur.lastrowid


def get_account_by_email(email: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_account(account_id: int):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None


def create_session(token_hash: str, account_id: int, ttl_seconds: int) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, account_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, account_id, now + ttl_seconds, now),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))


def get_session_account(token_hash: str):
    """按令牌取账号（同时校验有效期与会员态），无效返回 None。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, time.time()),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


# ---------- 会员 ----------

def extend_membership(account_id: int, days: int) -> float:
    """会员到期时间 = max(当前时间, 现有到期) + days 天；返回新的到期时间戳。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE accounts SET vip_expires_at = MAX(COALESCE(vip_expires_at, 0), ?) + ? "
            "WHERE id = ?",
            (time.time(), days * 86400, account_id),
        )
        row = conn.execute(
            "SELECT vip_expires_at FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return row["vip_expires_at"]


def set_membership_expiry(account_id: int, expires_at: float | None) -> None:
    """直接设置到期时间（测试与管理员调整用，正常续期走 extend_membership）。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE accounts SET vip_expires_at = ? WHERE id = ?", (expires_at, account_id)
        )


def is_vip(account: dict) -> bool:
    expires = account.get("vip_expires_at")
    return expires is not None and expires > time.time()


# ---------- 订单 ----------

def create_order(order_id: str, account_id: int, plan_id: str, amount: int,
                 currency: str, stripe_session_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO orders (id, account_id, plan_id, amount, currency, "
            "stripe_session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, account_id, plan_id, amount, currency, stripe_session_id, time.time()),
        )


def get_order(order_id: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def get_pending_order(account_id: int, plan_id: str):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE account_id = ? AND plan_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, plan_id),
        ).fetchone()
        return dict(row) if row else None


def mark_order_paid(order_id: str) -> bool:
    """幂等状态机：pending → paid 只允许发生一次（并发/重投递下仅首个成功）。"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ? WHERE id = ? AND status = 'pending'",
            (time.time(), order_id),
        )
        return cur.rowcount == 1


def set_order_session(order_id: str, stripe_session_id: str) -> None:
    """原会话过期后，在同一订单上换发新 Checkout 会话。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE orders SET stripe_session_id = ? WHERE id = ?",
            (stripe_session_id, order_id),
        )


# ---------- Webhook 去重 ----------

def record_event(event_id: str) -> bool:
    """首次见到该事件返回 True；重复投递返回 False（幂等跳过）。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO webhook_events (event_id, received_at) VALUES (?, ?)",
            (event_id, time.time()),
        )
        return cur.rowcount == 1


def forget_event(event_id: str) -> None:
    """履约中途失败时移除事件记录，让 Stripe 的重试能重新处理。"""
    with _conn() as conn:
        conn.execute("DELETE FROM webhook_events WHERE event_id = ?", (event_id,))


# ---------- AI 免费额度 ----------

def quota_used(subject: str, day: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT used FROM quota_usage WHERE subject = ? AND day = ?", (subject, day)
        ).fetchone()
        return row["used"] if row else 0


def quota_increment(subject: str, day: str) -> int:
    """计数 +1 并返回当日已用次数（UPSERT 原子自增）。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO quota_usage (subject, day, used) VALUES (?, ?, 1) "
            "ON CONFLICT(subject, day) DO UPDATE SET used = used + 1",
            (subject, day),
        )
        row = conn.execute(
            "SELECT used FROM quota_usage WHERE subject = ? AND day = ?", (subject, day)
        ).fetchone()
        return row["used"]


# ---------- 激活码 ----------

def add_activation_code(code: str, days: int, order_id: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO activation_codes (code, days, order_id, created_at) VALUES (?, ?, ?, ?)",
            (code, days, order_id, time.time()),
        )


def get_activation_code(code: str):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM activation_codes WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None


def get_activation_code_by_order(order_id: str):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM activation_codes WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None


def redeem_code(code: str, account_id: int, days: int, single_use: bool) -> bool:
    """兑换激活码并给账号续期，返回是否成功。

    single_use=True：订单码，全局只能被兑换一次；
    single_use=False：平台演示码，同一账号限兑一次。
    BEGIN IMMEDIATE 保证并发下的校验与写入是同一事务。
    """
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            exists = conn.execute(
                "SELECT 1 FROM code_redemptions WHERE code = ?"
                + (" AND account_id = ?" if not single_use else ""),
                (code, account_id) if not single_use else (code,),
            ).fetchone()
            if exists:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO code_redemptions (code, account_id, redeemed_at) VALUES (?, ?, ?)",
                (code, account_id, time.time()),
            )
            conn.execute(
                "UPDATE accounts SET vip_expires_at = MAX(COALESCE(vip_expires_at, 0), ?) + ? "
                "WHERE id = ?",
                (time.time(), days * 86400, account_id),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def new_activation_code(prefix: str = "SD") -> str:
    """生成对用户友好的兑换码：SD-XXXXXX-XXXX（大写无易混字符）。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part = lambda n: "".join(secrets.choice(alphabet) for _ in range(n))  # noqa: E731
    return f"{prefix}-{part(6)}-{part(4)}"
