"""LLM 配置加载：环境变量优先，其次 backend/local_config.py（gitignored，绝不入库）。

开闭原则：本模块独立读取 AI 相关配置，不修改 config.py。
支持的变量（均可通过 env 或 local_config.py 覆盖）：
- LLM_API_KEY            必填（缺失时 AI 功能报错提示，不做本地降级）
- LLM_BASE_URL           默认智谱 OpenAI 兼容端点
- LLM_MODEL              默认 glm-4-flash（免费档，128K 上下文）
- LLM_TIMEOUT_SECONDS    单次调用超时
- LLM_MAX_TOKENS         单次最大输出 token
"""
import os

import config
from downloader import BusinessError

LOCAL_CONFIG = config.BASE_DIR / "backend" / "local_config.py"

_DEFAULTS = {
    "LLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/",
    "LLM_MODEL": "glm-4-flash",
}
_INT_KEYS = ("LLM_TIMEOUT_SECONDS", "LLM_MAX_TOKENS")
_INT_DEFAULTS = {"LLM_TIMEOUT_SECONDS": 90, "LLM_MAX_TOKENS": 4096}


def _load_local_overrides() -> dict:
    """local_config.py 只是个赋值了上述变量的纯 Python 文件。"""
    if not LOCAL_CONFIG.is_file():
        return {}
    ns: dict = {}
    try:
        exec(LOCAL_CONFIG.read_text(encoding="utf-8"), {}, ns)  # noqa: S102 本地受信文件
    except Exception as e:
        raise BusinessError(f"backend/local_config.py 解析失败：{e}")
    keys = set(_DEFAULTS) | set(_INT_DEFAULTS) | {"LLM_API_KEY"}
    return {k: v for k, v in ns.items() if k in keys and v}


def get_llm_settings() -> dict:
    local = _load_local_overrides()

    def pick(key, default=None):
        v = os.getenv(key)
        if v:
            return v
        return local.get(key, default)

    settings = {k: pick(k, v) for k, v in _DEFAULTS.items()}
    settings["LLM_API_KEY"] = pick("LLM_API_KEY", "")
    for k, v in _INT_DEFAULTS.items():
        try:
            settings[k] = int(pick(k, v))
        except (TypeError, ValueError):
            settings[k] = v

    if not settings["LLM_API_KEY"]:
        raise BusinessError(
            "AI 功能未配置 LLM Key：请在 backend/local_config.py 中填写 LLM_API_KEY"
            "（智谱开放平台 open.bigmodel.cn 可免费申请）"
        )
    return settings
