# local_config.py 的提交版模板：复制本文件为 backend/local_config.py 并填入真实值。
# local_config.py 已被 .gitignore 忽略，绝不会入库；本模板只放占位符。

# ---- LLM（AI 总结/问答，OpenAI 兼容协议）----
LLM_API_KEY = "sk-你的智谱APIKey（open.bigmodel.cn 免费申请）"
# 可选覆盖（有默认值）：
# LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
# LLM_MODEL = "glm-4-flash"
# LLM_TIMEOUT_SECONDS = 90
# LLM_MAX_TOKENS = 4096

# ---- Stripe 会员支付（测试模式即可跑通全流程，见 docs/Stripe接入指南.md）----
STRIPE_SECRET_KEY = "sk_test_从Dashboard的Developers-APIkeys复制"
# webhook 密钥：本地联调用 `stripe listen` 打印的 whsec_...；
# 生产用 Dashboard Webhook endpoint 的签名密钥
STRIPE_WEBHOOK_SECRET = "whsec_从stripe listen输出或Dashboard复制"
