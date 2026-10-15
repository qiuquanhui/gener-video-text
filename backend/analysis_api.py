"""视频内容任务：默认仅提取文本，用户请求后复用字幕生成总结和导图。

AI 总结阶段并发两次 LLM 调用：结构化 JSON（摘要/章节/关键词）+ 总结文章
（SSE 流式）。文章通过 GET /api/analysis/{id}/article-stream 逐段推送，
也随任务轮询接口一并返回；文章失败自动降级为章节要点拼接。
"""
import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
import summarizer
from downloader import BusinessError
from subtitle_cues import extract_cues
from summarize_api import _validate_url

router = APIRouter()
logger = logging.getLogger(__name__)

# public_task 不暴露的内部字段（article_event 是 SSE 唤醒句柄）
_INTERNAL_KEYS = {"meta", "finished", "client_ip", "url", "article_event"}


class AnalysisRequest(BaseModel):
    url: str
    include_summary: bool = False


class AnalysisService:
    def __init__(self):
        self.tasks = {}
        self.workers = set()
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

    def cleanup(self):
        now = time.time()
        for task_id, task in list(self.tasks.items()):
            if task["finished"] and now - task["finished"] > summarizer.SESSION_TTL_SECONDS:
                del self.tasks[task_id]

    def public_task(self, task_id):
        self.cleanup()
        task = self.tasks.get(task_id)
        if not task:
            raise HTTPException(404, "分析任务不存在或已过期，请重新解析视频")
        return {k: v for k, v in task.items() if k not in _INTERNAL_KEYS}

    def _spawn(self, task):
        worker = asyncio.create_task(self._run(task))
        self.workers.add(worker)
        worker.add_done_callback(self.workers.discard)

    def start(self, url, client_ip, include_summary=False):
        self.cleanup()
        if len(self.tasks) >= config.MAX_TASKS:
            completed = [t for t in self.tasks.values() if t["finished"]]
            if not completed:
                raise BusinessError("分析任务较多，请稍后重试")
            del self.tasks[min(completed, key=lambda t: t["finished"])["task_id"]]
        task = {
            "task_id": uuid.uuid4().hex,
            "status": "queued", "text_status": "pending",
            "summary_requested": include_summary,
            "summary_status": "pending" if include_summary else "idle",
            "text_result": None, "summary_result": None,
            "text_error": "", "summary_error": "",
            "article_status": "idle", "article": "", "article_degraded": False,
            "article_event": asyncio.Event(),
            "meta": None, "finished": None, "url": url, "client_ip": client_ip,
        }
        self.tasks[task["task_id"]] = task
        self._spawn(task)
        return self.public_task(task["task_id"])

    def retry(self, task_id, client_ip):
        self.public_task(task_id)
        task = self.tasks[task_id]
        if task["status"] != "error":
            return self.public_task(task_id)
        task.update(status="queued", summary_status="pending" if task["summary_requested"] else "idle",
                    summary_error="", finished=None, client_ip=client_ip)
        if task["summary_requested"]:
            task.update(article_status="pending", article="", article_degraded=False)
        if not task["meta"]:
            task.update(text_status="pending", text_error="")
        self._spawn(task)
        return self.public_task(task_id)

    def request_summary(self, task_id, client_ip):
        self.public_task(task_id)
        task = self.tasks[task_id]
        if task["summary_status"] in {"pending", "generating", "done"}:
            return self.public_task(task_id)
        task.update(summary_requested=True, summary_status="pending", summary_error="",
                    article_status="pending", article="", article_degraded=False,
                    client_ip=client_ip)
        # 提取中的任务会在文本就绪后继续；已完成的任务直接复用缓存。
        if task["status"] in {"done", "error"}:
            task.update(status="queued", finished=None)
            if not task["meta"]:
                task.update(text_status="pending", text_error="")
            self._spawn(task)
        return self.public_task(task_id)

    async def _run(self, task):
        async with self.semaphore:
            try:
                if not task["meta"]:
                    task.update(status="extracting", text_status="extracting")
                    meta = await extract_cues(task["url"])
                    text = "\n".join(c["text"] for c in meta["cues"])
                    task.update(meta=meta, text_status="done", text_result={
                        "title": meta["title"], "lang": meta.get("lang"),
                        "text": text, "chars": len(text),
                    })
                if not task["summary_requested"]:
                    task["status"] = "done"
                    return

                task.update(status="summarizing", summary_status="generating",
                            article_status="streaming")
                summarizer.check_rate_limit(task["client_ip"])

                # 文章流式回调：写任务字段并唤醒 SSE 订阅者（工作线程 → 主循环）
                loop = asyncio.get_running_loop()
                event = task["article_event"]

                def notify(text: str):
                    task["article"] = text
                    loop.call_soon_threadsafe(event.set)

                async def run_article():
                    return await asyncio.to_thread(
                        summarizer.generate_article_stream, task["meta"], notify)

                # 结构化 JSON 与流式文章并发执行，总耗时≈单次调用
                json_res, article = await asyncio.gather(
                    asyncio.to_thread(summarizer.summarize, task["meta"]),
                    run_article(),
                    return_exceptions=True,
                )

                if isinstance(json_res, BaseException):
                    raise json_res
                result = json_res

                degraded = not isinstance(article, str) or not article.strip()
                if degraded:
                    article = summarizer.degrade_article(
                        result.get("title", ""), result.get("summary", ""),
                        result.get("chapters", []))
                    task["article"] = article
                    event.set()
                result["article"] = article
                result["article_degraded"] = degraded
                task.update(status="done", summary_status="done", article_status="done",
                            article_degraded=degraded, summary_result=result)
                event.set()
            except Exception as exc:
                if not isinstance(exc, BusinessError):
                    logger.exception("Video analysis failed")
                message = str(exc) if isinstance(exc, BusinessError) else "视频分析失败，请稍后重试"
                task["status"] = "error"
                if task["text_status"] != "done":
                    task.update(text_status="error", text_error=message,
                                summary_status="error" if task["summary_requested"] else "idle",
                                summary_error="文本提取失败，暂时无法生成总结和思维导图" if task["summary_requested"] else "")
                else:
                    task.update(summary_status="error", summary_error=message)
                # 文章通道同步收尾：已流出的部分保留，未完成则标记失败
                if task["summary_requested"] and task["article_status"] == "streaming":
                    task["article_status"] = "done" if task["article"].strip() else "error"
                    task["article_event"].set()
            finally:
                task["finished"] = time.time()

    async def shutdown(self):
        workers = list(self.workers)
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


service = AnalysisService()


@router.post("/api/analyze")
async def analyze(req: AnalysisRequest, request: Request):
    return service.start(_validate_url(req.url), request.client.host if request.client else "unknown", req.include_summary)


@router.get("/api/analysis/{task_id}")
async def analysis_status(task_id: str):
    return service.public_task(task_id)


@router.post("/api/analysis/{task_id}/retry")
async def retry_analysis(task_id: str, request: Request):
    return service.retry(task_id, request.client.host if request.client else "unknown")


@router.post("/api/analysis/{task_id}/summarize")
async def summarize_analysis(task_id: str, request: Request):
    return service.request_summary(task_id, request.client.host if request.client else "unknown")


@router.get("/api/analysis/{task_id}/article-stream")
async def article_stream(task_id: str):
    """总结文章的 SSE 推送：每条消息都是「截至当前的完整文本 + 状态」，
    断线重连（EventSource 自动重试）天然幂等；终态（done/error）后关流。"""
    service.cleanup()
    task = service.tasks.get(task_id)
    if not task:
        raise HTTPException(404, "分析任务不存在或已过期，请重新解析视频")

    async def gen():
        event = task["article_event"]
        while True:
            event.clear()  # 先清后发：清除与快照之间到达的更新只多送一次，不丢更新
            payload = json.dumps({
                "status": task["article_status"],
                "text": task["article"],
                "degraded": task["article_degraded"],
            }, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            if task["article_status"] in ("done", "error"):
                break
            await event.wait()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # 反代场景禁用缓冲，保证逐段到达
    })
