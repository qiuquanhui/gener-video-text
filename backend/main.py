"""闪电下载 · FastAPI 入口：API 路由 + 前端静态托管（单进程启动）。"""
import asyncio
import re

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import douyin
import text_extractor
from downloader import BusinessError, DownloadService, sanitize_filename

URL_RE = re.compile(r"^https?://\S+$")

app = FastAPI(title="闪电下载", docs_url="/api/docs")
service = DownloadService()

# 阶段 4 新增：AI 总结 + AI 问答（独立路由模块，开闭原则纯增量接线）
import summarize_api  # noqa: E402
app.include_router(summarize_api.router)


class ParseRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "1080"
    title: str | None = None


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not URL_RE.match(url) or url.startswith("-"):
        raise HTTPException(400, "链接无效：请粘贴以 http(s):// 开头的完整视频页地址")
    # 抖音个人主页弹窗链接（user/self?modal_id=N）等形态统一规范化为视频页
    return douyin.normalize_douyin_url(url) or url


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "闪电下载"}


@app.post("/api/parse")
async def parse_api(req: ParseRequest):
    url = _validate_url(req.url)
    return await service.parse(url)


@app.post("/api/download")
async def download_api(req: DownloadRequest):
    url = _validate_url(req.url)
    if req.quality != "mp3" and not req.quality.isdigit():
        raise HTTPException(400, "清晰度参数无效")
    task = await service.start_download(url, req.quality, title=req.title)
    return {"task_id": task["id"], "status": task["status"]}


@app.get("/api/tasks/{task_id}")
async def task_api(task_id: str):
    view = service.public_task(task_id)
    if not view:
        raise HTTPException(404, "任务不存在或已过期")
    return view


@app.get("/api/file/{task_id}")
async def file_api(task_id: str):
    info = service.get_file_info(task_id)
    if not info:
        raise HTTPException(404, "任务不存在或未完成")
    path, name = info
    if not path.is_file():
        raise HTTPException(410, "文件已过期清理，请重新解析并下载")
    return FileResponse(str(path), filename=name, media_type="application/octet-stream")


@app.post("/api/extract-text")
async def extract_text_api(req: ParseRequest):
    url = _validate_url(req.url)
    return await text_extractor.extract_text(url)


@app.on_event("startup")
async def startup():
    service.cleanup()
    asyncio.create_task(service.cleanup_loop())


# 静态托管放最后，避免吞掉 /api 路由
app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
