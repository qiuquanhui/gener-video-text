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

# 会员模拟流程的激活码
VIP_CODES = {"VIP2026", "DOWN8888"}

# LLM（OpenAI 兼容协议）；实际配置由 llm_config 加载，无 Key 时 AI 报错并保留已提取文本。
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# 可选本地语音转写：只有在平台没有公开字幕时才使用；音频不发送给 LLM。
ASR_PYTHON = os.getenv("ASR_PYTHON", "")
ASR_MODEL = os.getenv("ASR_MODEL", "base")
ASR_MODEL_CACHE = os.getenv("ASR_MODEL_CACHE", "")
ASR_TIMEOUT_SECONDS = int(os.getenv("ASR_TIMEOUT_SECONDS", "900"))
