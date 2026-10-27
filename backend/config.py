"""全局配置：所有可调参数集中在此（目录/超时/并发/清理策略/阶段4预留）。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

HOST = "127.0.0.1"
PORT = 8000

# 下载任务
MAX_CONCURRENT_DOWNLOADS = 3   # 并发下载上限（其余排队）
MAX_TASKS = 100                # 内存任务数上限

# 超时（秒）
PARSE_TIMEOUT_SECONDS = 60
EXTRACT_TIMEOUT_SECONDS = 90
LINE_TIMEOUT_SECONDS = 120     # 子进程 stdout 无输出的兜底超时（防卡死）

# 临时文件生命周期
FILE_TTL_SECONDS = 2 * 60 * 60       # 文件保留 2 小时
CLEANUP_INTERVAL_SECONDS = 30 * 60   # 每 30 分钟清理一轮

# 会员与支付（阶段 5：邮箱账号 + Stripe 真实支付 + 免费额度制）
DATA_DIR = BASE_DIR / "data"                      # SQLite 数据目录（gitignored）
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}")  # 对外可达的基础 URL（success/cancel 回跳）
FREE_AI_PER_DAY = int(os.getenv("FREE_AI_PER_DAY", "3"))  # 免费用户每日 AI 次数（会员不限）
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60           # 登录会话有效期 30 天

# 在售套餐：金额单位为最小货币单位（CNY 即“分”，990 = ¥9.9）；
# 若 Stripe 账户地区不支持 cny 收款，仅需改此处 currency/amount（如 usd/199）。
PLANS = {
    "monthly": {
        "id": "monthly", "name": "会员月卡", "amount": 990, "currency": "cny", "days": 30,
    },
}

# 平台演示兑换码：默认关闭（生产环境不启用，公开仓库不含任何可用兑换码）。
# 本地开发/演示可用环境变量 VIP_CODES=A,B 临时开启；正式兑换码由订单履约生成（全局单次有效）。
VIP_CODES = {c.strip().upper() for c in os.getenv("VIP_CODES", "").split(",") if c.strip()}

# LLM（OpenAI 兼容协议）；实际配置由 llm_config 加载，无 Key 时 AI 报错并保留已提取文本。
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# 可选本地语音转写：只有在平台没有公开字幕时才使用；音频不发送给 LLM。
ASR_PYTHON = os.getenv("ASR_PYTHON", "")
ASR_MODEL = os.getenv("ASR_MODEL", "base")
ASR_MODEL_CACHE = os.getenv("ASR_MODEL_CACHE", "")
ASR_TIMEOUT_SECONDS = int(os.getenv("ASR_TIMEOUT_SECONDS", "900"))
