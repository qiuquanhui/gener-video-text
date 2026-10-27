"""Stripe 配置加载：环境变量优先，其次 backend/local_config.py（gitignored，绝不入库）。

开闭原则：本模块独立读取支付配置，仿 llm_config 模式，不修改 config.py。
支持的变量：
- STRIPE_SECRET_KEY      测试模式 sk_test_...（支付功能的启用开关）
- STRIPE_WEBHOOK_SECRET  本地联调 whsec_...（stripe listen 打印）或 Dashboard 端点密钥
"""
import os

import config
from downloader import BusinessError

LOCAL_CONFIG = config.BASE_DIR / "backend" / "local_config.py"

_KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")


def _load_local_overrides() -> dict:
    if not LOCAL_CONFIG.is_file():
        return {}
    ns: dict = {}
    try:
        exec(LOCAL_CONFIG.read_text(encoding="utf-8"), {}, ns)  # noqa: S102 本地受信文件
    except Exception as e:
        raise BusinessError(f"backend/local_config.py 解析失败：{e}")
    return {k: v for k, v in ns.items() if k in _KEYS and v}


def get_stripe_settings() -> dict:
    local = _load_local_overrides()

    def pick(key):
        return os.getenv(key) or local.get(key) or ""

    return {"secret_key": pick("STRIPE_SECRET_KEY"), "webhook_secret": pick("STRIPE_WEBHOOK_SECRET")}


def stripe_enabled() -> bool:
    return bool(get_stripe_settings()["secret_key"])
