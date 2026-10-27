# Stripe 接入指南（第一次接入必读 · 手把手版）

> 目标读者：从未接过 Stripe 的开发者。照着做，**不需要任何真实付款、不需要境外银行卡**，
> 用官方测试模式 + 测试卡号就能在本地把「注册 → 下单 → 支付 → 开通会员」完整走通。
> 代码已全部实现并有 62 个后端测试覆盖，你只需要完成本文的配置步骤。

---

## 0. 整个支付是怎么运作的（1 分钟版）

```
浏览器                     你的服务器                      Stripe
  │  ①点"前往支付" ── POST /api/vip/checkout ──▶│
  │                                            │ ②创建 Checkout Session ─▶│
  │ ③跳转到 Stripe 托管收银台 ◀── checkout_url ──│◀─────────────────────────│
  │──────────── ④在 Stripe 页面输入测试卡号 ────────────────────────────────▶│
  │ ⑤支付完成，跳回 /pay/success.html?order=xxx │                           │
  │                                            │ ◀── ⑥webhook 通知支付成功 ─│
  │                                            │ ⑦验签→去重→订单置 paid     │
  │ ⑧页面轮询订单状态 ── GET /api/vip/order ──▶ │    →会员+30天→生成兑换码   │
  │ ◀── "会员开通成功 🎉" ──────────────────────│                           │
```

要点：**金额由服务器定义**（改不了）、**会员只在 Stripe 确认支付后开通**（前端说成功不算数）、
**同一事件重发一百遍也只加一次会员**（幂等，有测试保证）。

---

## 1. 你需要做的事（共 6 步，约 20 分钟）

### 第 1 步：注册 Stripe 账户（免费，5 分钟）

1. 打开 https://dashboard.stripe.com/register
2. 填邮箱、姓名、国家（选你方便的地区，如 Hong Kong / US；**测试模式不要求绑卡、不要求公司资料**）
3. 邮箱收验证邮件并点击确认
4. 登录后，**确认左上角（或设置里）处于 "Test mode / 测试模式"**（有个开关，默认就是测试模式）

> 网络说明：国内一般可直接访问 `dashboard.stripe.com` 和 `api.stripe.com`；
> 如果打不开，切换网络或使用代理后再试。测试模式全程**不会产生任何真实扣款**。

### 第 2 步：拿到「Secret Key」

1. 在 Dashboard 进入 **Developers → API keys**（地址栏直接访问 https://dashboard.stripe.com/test/apikeys）
2. 复制 **Secret key**，长这样：`sk_test_开头的一长串`
3. ⚠️ 这个 key 等同于你账户的"钱包钥匙"：**不要发给任何人、不要写进任何会提交 git 的文件**

### 第 3 步：把密钥写进本地配置（30 秒）

编辑 `backend/local_config.py`（此文件已在 `.gitignore`，永远不会被提交），追加两行：

```python
# Stripe 测试模式密钥（docs/Stripe接入指南.md）
STRIPE_SECRET_KEY = "sk_test_你复制的那串"
STRIPE_WEBHOOK_SECRET = "whsec_先占位，第 5 步会替换"
```

> 前缀判断：`sk_test_` = 测试模式（放心用）；`sk_live_` = 生产模式（钱会真的扣，本项目现阶段别用）。

### 第 4 步：安装 Stripe CLI（用于把 Stripe 的事件转发到你本机，3 分钟）

CLI 的作用：本地开发时 Stripe 无法主动访问 `localhost`，CLI 帮你"转接"。

Windows 任选一种：

```bash
# 方式 A：scoop（推荐，如果没有 scoop 先 https://scoop.sh 装一下）
scoop install stripe

# 方式 B：choco
choco install stripe-cli

# 方式 C：手动下载 exe（最省事）
# https://github.com/stripe/stripe-cli/releases/latest
# 下载 stripe_X.X.X_windows_x86_64.zip，解压出的 stripe.exe 放进任意 PATH 目录
# （例如 C:\Windows\System32 不推荐；建议 D:\tools\ 并把 D:\tools 加入 PATH）
```

装好后验证：

```bash
stripe version
```

### 第 5 步：登录并启动事件转发（2 分钟，保持窗口开着）

```bash
# ① 浏览器授权（会自动打开浏览器登录你的 Stripe 账号并按 Enter 回车确认）
stripe login

# ② 把 webhook 事件转发到本地服务（服务需已启动：python backend/main.py）
stripe listen --forward-to localhost:8000/api/webhooks/stripe
```

`listen` 启动后会打印一行：

```
> Ready! Your webhook signing secret is whsec_xxxxxxxxxxxx (^C to quit)
```

**把这串 `whsec_...` 复制，替换第 3 步里 `STRIPE_WEBHOOK_SECRET` 的占位值**，然后重启后端服务。

> 原理：`whsec_` 是这对「CLI↔你的服务」之间的暗号，后端用它验证"这个请求真的来自 Stripe"。
> 两边不一致 → 后端返回 400（签名校验失败）→ 看到这个错就回来核对这里。

### 第 6 步：完整走一遍支付（5 分钟）

1. 启动服务：双击 `start.bat`（或 `.venv/Scripts/python.exe backend/main.py`）
2. 浏览器打开 http://127.0.0.1:8000
3. 点导航栏「登录 / 注册」→ 注册一个账号（邮箱随意如 `test@test.com`，密码 ≥8 位）
4. 点「🔓 开通会员」→「💳 前往支付」→ 跳转到 Stripe 收银台页面
5. 在收银台填 **测试卡信息**（随便编，不扣钱）：

| 字段 | 填什么 | 说明 |
|---|---|---|
| Email | 任意邮箱 | |
| Card number | `4242 4242 4242 4242` | 万能成功卡 |
| Expiry | 任意**未来**日期，如 `12/34` | |
| CVC | 任意 3 位，如 `123` | |
| Name / postal | 任意 | |

6. 点 Pay → 显示 `Payment successful` → 自动跳回 `/pay/success.html`
7. 等几秒，页面显示 **「会员开通成功 🎉」+ 备用兑换码**；`stripe listen` 窗口会滚出 `checkout.session.completed` 事件
8. 回首页：导航栏出现 👑 会员徽章；再点 AI 总结不再消耗每日额度

**想测"支付失败"？** 用拒付卡 `4000 0000 0000 0002` 重复上面步骤 → 收银台报错Declined → 订单仍是 pending，会员不开通（这正是防重复/防白嫖的验证点）。

---

## 2. 常见问题（FAQ）

### Q1：我的 Stripe 账户能收 CNY（人民币）吗？
Dashboard → Settings → **Currencies / 货币** 查看"支持的展示货币"列表。
若列表里没有 CNY：打开 `backend/config.py`，把 `PLANS` 改成 USD 一行搞定：

```python
PLANS = {
    "monthly": {"id": "monthly", "name": "会员月卡", "amount": 199, "currency": "usd", "days": 30},
}
```

（`amount` 单位是"最小货币单位"：USD 的 $1.99 = 199；CNY 的 ¥9.9 = 990。）
前端价格文案在 `frontend/index.html` 定价区同步改一下即可。

### Q2：支付成功了但页面一直转圈"确认中"？
三查：
1. `stripe listen` 窗口是否还开着？（关了就收不到 webhook）
2. `STRIPE_WEBHOOK_SECRET` 是否与 listen 打印的 `whsec_` 一致？改过要**重启后端**
3. 即便 webhook 彻底丢失也不怕：成功页每 2 秒轮询一次订单，后端会**主动向 Stripe 查证**
   并开通会员（这叫"兜底"，专门为这种情况设计的）；真扣款了绝不会丢会员。

### Q3：怎么验证"重复回调不会重复加会员"？
`stripe listen` 窗口里找到那条 `evt_...` 事件 ID，执行：

```bash
stripe events resend evt_xxxxxxxxx
```

再回到首页刷新：会员到期时间**不会**从 +30 天变成 +60 天（有自动化测试
`test_duplicate_webhook_delivery_extends_only_once` 保证这一点）。

### Q4：没有 Stripe 账号 / 完全无外网，怎么演示会员功能？
用平台演示兑换码：登录后点「开通会员」→ 兑换码输入 `VIP2026`（或 `DOWN8888`），
每账号限兑一次、各送 30 天会员——这是服务端校验的真实会员态，不是前端模拟。

### Q5：怎么清空测试数据从头再来？
停掉服务，删除 `data/app.db*` 三个文件（SQLite 库+WAL），重启即可。
（账号、订单、额度、会员全在这个文件里，删了不影响代码。）

### Q6：AI 免费额度是怎么算的？
- 未登录：按 **IP** 每天 3 次（AI 总结/问答/解析带总结共用，文本提取与下载不计）
- 已登录非会员：按 **账号** 每天 3 次（跨设备共享，每天 0 点重置）
- 会员：不限次
- 第 4 次请求返回 402，前端自动弹登录框（或会员引导）

---

## 3. 上线生产前 Checklist（现在不用做，但先记下）

- [ ] Stripe Dashboard 创建正式 **Webhook endpoint** 指向 `https://你的域名/api/webhooks/stripe`，
      事件勾选 `checkout.session.completed` 与 `checkout.session.async_payment_succeeded`，
      并把它的 `whsec_` 用于生产配置（CLI 的 whsec 只对本地有效）
- [ ] 换成 `sk_live_` 密钥（**生产强烈建议**密钥放环境变量而非文件）
- [ ] `backend/config.py` 的 `PUBLIC_BASE_URL` 改为正式域名（回跳地址由它拼出）
- [ ] 全站 HTTPS，并把 `auth_api.py` 中 `set_cookie` 的 `secure=True` 打开（代码有注释标记位置）
- [ ] 确认 Stripe 账户已完成激活（正式收款需要）且支持所选货币
- [ ] 生产库 `data/app.db` 做好备份策略（会员权益=资金凭证，丢了要赔付）

## 4. 安全设计速览（为什么这么实现）

| 措施 | 防什么 |
|---|---|
| Webhook HMAC 验签 + 300 秒时间窗 | 伪造回调、录制重放 |
| `webhook_events` 表按事件 ID 去重 | Stripe 重发事件导致重复履约 |
| 订单 `pending→paid` 条件更新（单行事务） | 并发回调双重续期 |
| 每「账号+套餐」唯一 pending 订单 + 复用收银台链接 | 开两个收银台重复付款 |
| 金额/币种只存服务端 `config.PLANS` | 客户端改价 0.01 元买会员 |
| 密码 scrypt 加盐、Cookie HttpOnly+ SameSite=Lax | 拖库撞库、XSS 偷会话 |
| 登录接口限流 10 次/分/IP | 撞库爆破 |

完整设计见 `docs/方案设计-会员支付.md`。
