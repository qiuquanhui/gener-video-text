"""AI 总结与 AI 问答（阶段 4）：GLM（OpenAI 兼容协议）调用封装。

职责：
- summarize(meta)：把带时间轴字幕转写喂给 GLM，产出结构化 JSON
  （一句话摘要 / 时间戳章节要点 / 关键词 / 思维导图树）
- answer_question(...)：基于同一份转写做多轮追问回答（引用时间戳，不编造）
- 转写会话缓存：总结后 60 分钟内问答免重复提取字幕
- IP 限流：总结 + 问答共用每分钟 3 次（免费开放的防滥用底线）

实现约束：纯 stdlib（urllib）调 LLM，零新增依赖；提示词驱动严格 JSON
（智谱官方最佳实践），容错解析 + 瞬时故障自动重试（总时限兜底防挂死）。
"""
import collections
import concurrent.futures
import http.client
import json
import re
import time
import urllib.error
import urllib.request
import uuid

from downloader import BusinessError
from llm_config import get_llm_settings

MAX_TRANSCRIPT_CHARS = 30000   # 超长视频截断（glm-4-flash 128K 上下文充足，此为输出质量保护）
SESSION_TTL_SECONDS = 60 * 60  # 总结→问答的转写缓存有效期
SESSION_MAX = 50               # 会话缓存上限（超出淘汰最旧）
RATE_LIMIT, RATE_WINDOW = 3, 60  # 每 IP 每分钟次数
LLM_ATTEMPTS = 3               # 瞬时失败（服务繁忙/网络中断）自动重试总次数
RETRYABLE_HTTP_CODES = {502, 503, 504}

# ---------- 限流 ----------

_rate_hits: dict[str, collections.deque] = {}


def check_rate_limit(client_ip: str):
    now = time.time()
    dq = _rate_hits.setdefault(client_ip, collections.deque())
    while dq and now - dq[0] > RATE_WINDOW:
        dq.popleft()
    if len(dq) >= RATE_LIMIT:
        raise BusinessError(f"请求太频繁啦，AI 功能限每分钟 {RATE_LIMIT} 次，请稍后再试")
    dq.append(now)


# ---------- 转写会话缓存 ----------

_sessions: dict[str, dict] = {}


def _new_session(transcript: str, title: str) -> str:
    now = time.time()
    for sid in [s for s, v in _sessions.items() if now - v["created"] > SESSION_TTL_SECONDS]:
        _sessions.pop(sid, None)
    while len(_sessions) >= SESSION_MAX:
        _sessions.pop(next(iter(_sessions)), None)
    sid = uuid.uuid4().hex[:16]
    _sessions[sid] = {"transcript": transcript, "title": title, "created": now}
    return sid


def _get_session(session_id: str) -> dict:
    s = _sessions.get(session_id or "")
    if not s or time.time() - s["created"] > SESSION_TTL_SECONDS:
        raise BusinessError("总结会话已过期或不存在（缓存 60 分钟），请重新生成 AI 总结")
    return s


# ---------- GLM 调用 ----------

# HTTP 调用放独立线程执行：urllib 的 timeout 只约束单次 socket 读，流式/缓慢
# 滴流的响应可无限重置它导致挂死；外层用 future.result(timeout) 施加总时限。
_HTTP_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm-http")


def _post_json(url: str, body: bytes, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chat(messages: list[dict], json_mode: bool = False) -> str:
    s = get_llm_settings()
    payload = {
        "model": s["LLM_MODEL"],
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": s["LLM_MAX_TOKENS"],
    }
    # DeepSeek OpenAI-compatible endpoint supports constrained JSON output.
    if json_mode and "api.deepseek.com" in s["LLM_BASE_URL"].lower():
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    url = s["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + s["LLM_API_KEY"],
    }
    attempt_timeout = float(s["LLM_TIMEOUT_SECONDS"])

    # 服务繁忙（503 等）、响应中断（IncompleteRead）、读超时、滴流挂死均属瞬时
    # 故障：总时限兜底 + 自动重试，避免用户面对无限挂起后的一次性失败。
    for attempt in range(LLM_ATTEMPTS):
        try:
            future = _HTTP_POOL.submit(_post_json, url, body, headers, attempt_timeout)
            data = future.result(timeout=attempt_timeout + 5)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "ignore")).get("error", {}).get("message", "")
            except Exception:
                pass
            if e.code in RETRYABLE_HTTP_CODES and attempt < LLM_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            hint = "，建议稍后重试或临时切换 LLM 服务商" if e.code in RETRYABLE_HTTP_CODES else ""
            raise BusinessError(f"LLM 服务返回错误（HTTP {e.code}）：{detail or '请稍后重试'}{hint}")
        except (http.client.HTTPException, urllib.error.URLError, TimeoutError,
                concurrent.futures.TimeoutError, ConnectionError) as e:
            if attempt < LLM_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise BusinessError(
                f"LLM 服务连接超时或中断（{type(e).__name__}），已自动重试 {LLM_ATTEMPTS} 次仍失败；"
                "服务商可能繁忙，请稍后重试或临时切换 LLM 服务商"
            )
        except json.JSONDecodeError:
            raise BusinessError("LLM 服务响应异常，请重试")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise BusinessError("LLM 响应格式异常，请重试")


# ---------- 转写组装 ----------

def _fmt_ts(sec: float) -> str:
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_transcript(cues: list[dict]) -> tuple[str, bool]:
    """cues → "[mm:ss] 文本" 行流；返回 (transcript, 是否截断)。"""
    parts, total = [], 0
    truncated = False
    for c in cues:
        line = f"[{_fmt_ts(c['start'])}] {c['text']}"
        if total + len(line) > MAX_TRANSCRIPT_CHARS:
            truncated = True
            break
        parts.append(line)
        total += len(line) + 1
    return "\n".join(parts), truncated


# ---------- 总结 ----------

_SUMMARY_SYSTEM = (
    "你是专业的视频内容编辑，擅长把视频字幕整理为结构化学习笔记。"
    "你必须只输出一个合法的 JSON 对象，不要输出任何其他文字、解释或代码块标记。"
)

_SUMMARY_USER_TMPL = """视频标题：{title}

带时间戳的字幕转写（[分:秒] 为该句出现时间）：
{transcript}
{trunc_note}
请基于以上转写生成 JSON（全部中文）：
{{
  "summary": "一句话概括视频核心内容，80 字以内",
  "chapters": [
    {{"title": "章节标题（12 字以内）", "start": 12.4, "points": ["该章关键要点（40 字以内，最多 4 条）"]}}
  ],
  "keywords": ["关键词（5-8 个）"]
}}

硬性要求：
1. chapters 按内容划分 3-8 个章节（短视频可用 1-2 个）；start 为该章起始秒数（取转写时间戳，纯数字）
2. 要点必须来自转写内容，禁止编造
3. 只输出这个 JSON 对象，第一个字符必须是 {{ ，最后一个字符必须是 }}"""

# 思维导图不再让模型生成（与 chapters 完全重复、输出 token 翻倍拖慢速度），
# 由 _normalize_summary 基于 chapters 本地构建，结构与此前完全一致。

_RETRY_USER = (
    "你上次输出的不是合法 JSON 或缺少必要字段。"
    "请重新只输出符合上述结构要求的 JSON 对象，第一个字符必须是 { ，最后一个字符必须是 } ，不要包含任何其他文字。"
)


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no json object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _norm_str_list(v, limit, maxlen) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip()[:maxlen] for x in v if str(x).strip()][:limit]


def _normalize_mindmap(node, depth=0):
    if not isinstance(node, dict):
        return None
    label = str(node.get("label") or "").strip()[:12]
    if not label:
        return None
    result = {"label": label}
    children = node.get("children")
    if depth < 2 and isinstance(children, list):
        normalized = [_normalize_mindmap(child, depth + 1) for child in children[:8]]
        result["children"] = [child for child in normalized if child]
    return result


def _normalize_summary(data: dict, fallback_title: str) -> dict:
    chapters_raw = data.get("chapters")
    if not isinstance(chapters_raw, list):
        raise ValueError("chapters missing")
    chapters = []
    for ch in chapters_raw:
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip()[:40]
        points = _norm_str_list(ch.get("points"), 4, 120)
        if not title or not points:
            continue
        try:
            start = round(float(ch.get("start")), 1)
        except (TypeError, ValueError):
            start = 0.0
        chapters.append({"title": title, "start": max(0.0, start), "points": points})
    if not chapters:
        raise ValueError("no valid chapters")
    chapters.sort(key=lambda c: c["start"])

    mindmap = _normalize_mindmap(data.get("mindmap"))
    if not mindmap or not mindmap.get("children"):
        mindmap = {
            "label": fallback_title[:12] or "视频主题",
            "children": [
                {"label": ch["title"][:12], "children": [{"label": p[:12]} for p in ch["points"]]}
                for ch in chapters
            ],
        }

    return {
        "summary": str(data.get("summary") or "").strip()[:300] or "（模型未返回摘要）",
        "chapters": chapters,
        "keywords": _norm_str_list(data.get("keywords"), 8, 24),
        "mindmap": mindmap,
        "_fallback_title": fallback_title,
    }


def summarize(meta: dict) -> dict:
    """meta 来自 subtitle_cues.extract_cues；同步阻塞函数，上层用 to_thread 调。"""
    settings = get_llm_settings()  # 先校验 Key，避免白提取一轮字幕
    t0 = time.time()
    transcript, truncated = build_transcript(meta["cues"])
    content = _SUMMARY_USER_TMPL.format(
        title=meta["title"],
        transcript=transcript,
        trunc_note="\n（注意：转写超长已截断，请仅基于给出的部分总结）\n" if truncated else "",
    )
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": content},
    ]

    last_err = None
    for attempt in range(2):
        raw = _chat(messages, json_mode=True)
        try:
            result = _normalize_summary(_extract_json(raw), meta["title"])
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            messages = messages[:2] + [
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": _RETRY_USER},
            ]
    else:
        raise BusinessError(f"AI 总结生成失败（模型未返回合规 JSON，可重试）：{last_err}")

    session_id = _new_session(transcript, meta["title"])
    result.pop("_fallback_title", None)
    result.update({
        "session_id": session_id,
        "title": meta["title"],
        "lang": meta.get("lang"),
        "chars": meta["chars"],
        "transcript": "\n".join(c["text"] for c in meta["cues"]),
        "elapsed": round(time.time() - t0, 1),
        "model": settings["LLM_MODEL"],
    })
    return result


# ---------- AI 问答 ----------

def _qa_system(transcript: str, title: str) -> str:
    return f"""你是视频内容的问答助手。视频标题：《{title}》
以下是该视频的字幕转写（[分:秒] 为出现时间）：

{transcript}

回答规则：
1. 只依据转写内容回答，回答中尽量引用相关时间戳（如 [03:25]）
2. 转写中没有相关信息时，明确说"视频中没有提到相关内容"，禁止编造
3. 中文回答，简洁清晰（一般不超过 300 字）"""


def _sanitize_history(history) -> list[dict]:
    msgs = []
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "").strip()[:2000]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs[-6:]


def answer_question(session_id: str, question: str, history) -> dict:
    settings = get_llm_settings()
    session = _get_session(session_id)
    q = (question or "").strip()
    if not q:
        raise BusinessError("请输入问题")
    if len(q) > 500:
        raise BusinessError("问题太长了（限 500 字）")

    t0 = time.time()
    messages = [
        {"role": "system", "content": _qa_system(session["transcript"], session["title"])},
        *_sanitize_history(history),
        {"role": "user", "content": q},
    ]
    answer = _chat(messages).strip()
    if not answer:
        raise BusinessError("AI 未返回回答，请重试")
    return {"answer": answer, "elapsed": round(time.time() - t0, 1), "model": settings["LLM_MODEL"]}
