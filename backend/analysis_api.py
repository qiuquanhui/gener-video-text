"""视频内容任务：默认仅提取文本，用户请求后复用字幕生成总结和导图。"""
import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import config
import summarizer
from downloader import BusinessError
from subtitle_cues import extract_cues
from summarize_api import _validate_url

router = APIRouter()
logger = logging.getLogger(__name__)


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
        return {k: v for k, v in task.items() if k not in {"meta", "finished", "client_ip", "url"}}

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
        if not task["meta"]:
            task.update(text_status="pending", text_error="")
        self._spawn(task)
        return self.public_task(task_id)

    def request_summary(self, task_id, client_ip):
        self.public_task(task_id)
        task = self.tasks[task_id]
        if task["summary_status"] in {"pending", "generating", "done"}:
            return self.public_task(task_id)
        task.update(summary_requested=True, summary_status="pending", summary_error="", client_ip=client_ip)
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
                task.update(status="summarizing", summary_status="generating")
                summarizer.check_rate_limit(task["client_ip"])
                result = await asyncio.to_thread(summarizer.summarize, task["meta"])
                task.update(status="done", summary_status="done", summary_result=result)
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
