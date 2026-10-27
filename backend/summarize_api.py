"""AI 总结 + AI 问答 路由（阶段 4，免费开放）。

开闭原则：独立 APIRouter，main.py 仅追加 include_router，不改动既有路由。
"""
import asyncio
import re
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel

import douyin
import quota
import summarizer
from downloader import BusinessError
from subtitle_cues import extract_cues

router = APIRouter()

URL_RE = re.compile(r"^https?://\S+$")


class SummarizeRequest(BaseModel):
    url: str


class QARequest(BaseModel):
    session_id: str = ""
    question: str = ""
    history: list = []


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not URL_RE.match(url) or url.startswith("-"):
        raise BusinessError("链接无效：请粘贴以 http(s):// 开头的完整视频页地址")
    return douyin.normalize_douyin_url(url) or url


@router.post("/api/summarize")
async def summarize_api(req: SummarizeRequest, request: Request):
    summarizer.check_rate_limit(request.client.host if request.client else "unknown")
    quota.consume_ai_quota(request)                # 免费 3 次/天（会员不限），超限 402
    url = _validate_url(req.url)
    meta = await extract_cues(url)          # 字幕提取（异步子进程）
    result = await asyncio.to_thread(summarizer.summarize, meta)  # LLM 调用（阻塞→线程）
    return result


@router.post("/api/qa")
async def qa_api(req: QARequest, request: Request):
    summarizer.check_rate_limit(request.client.host if request.client else "unknown")
    quota.consume_ai_quota(request)                # 每轮追问计 1 次
    return await asyncio.to_thread(
        summarizer.answer_question, req.session_id, req.question, req.history
    )
