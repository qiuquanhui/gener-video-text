"""本地 Whisper 语音转写回退；音频与模型推理不发送给远端服务。"""
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

import config
from downloader import BusinessError, YT_DLP, _friendly_error


WHISPER_SCRIPT = r'''
import json, os, sys
import torch, whisper

model_name, audio_path, cache_dir = sys.argv[1:]
torch.set_num_threads(min(8, os.cpu_count() or 4))
model = whisper.load_model(model_name, device="cpu", download_root=cache_dir or None)
result = model.transcribe(audio_path, language="zh", task="transcribe", fp16=False, temperature=0, verbose=False)
payload = {
    "text": result.get("text", "").strip(),
    "segments": [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result.get("segments", []) if s.get("text", "").strip()
    ],
}
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
'''


def _python_candidates() -> list[str]:
    candidates = []
    if config.ASR_PYTHON.strip():
        candidates.append(config.ASR_PYTHON.strip().strip('"'))
    path_python = shutil.which("python")
    if path_python:
        candidates.append(path_python)
    candidates.append(sys.executable)
    return list(dict.fromkeys(candidates))


async def _python_with_whisper() -> str:
    probe = "import torch, whisper"
    for python in _python_candidates():
        try:
            proc = await asyncio.create_subprocess_exec(
                python, "-c", probe,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await asyncio.wait_for(proc.wait(), timeout=10) == 0:
                return python
        except (OSError, asyncio.TimeoutError):
            continue
    raise BusinessError(
        "该视频没有公开字幕；本地语音转写尚未安装。安装 openai-whisper 和 PyTorch 后可自动转写，"
        "不需要 B 站 Cookie，音频也不会上传。"
    )


async def transcribe_url(url: str, workdir: Path) -> dict:
    """匿名下载音频并在本机 Whisper 转写，返回纯文本和时间轴 cues。"""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", config.ASR_MODEL):
        raise BusinessError("本地语音模型配置无效")
    python = await _python_with_whisper()
    audio_template = str(workdir / "audio.%(ext)s")
    proc = await asyncio.create_subprocess_exec(
        *YT_DLP, "--ignore-config", "--no-warnings", "--no-progress",
        "--no-playlist", "--socket-timeout", "20", "-f", "bestaudio",
        "-o", audio_template, url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=config.EXTRACT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BusinessError("音频获取超时，请稍后重试")
    if proc.returncode != 0:
        raise BusinessError(_friendly_error(err.decode("utf-8", "ignore")))

    audio_files = [p for p in workdir.glob("audio.*") if p.suffix.lower() not in {".part", ".json"}]
    if not audio_files:
        raise BusinessError("平台未返回可转写的音频流")

    cache_dir = config.ASR_MODEL_CACHE
    args = [
        python, "-c", WHISPER_SCRIPT, config.ASR_MODEL,
        str(audio_files[0]), cache_dir,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=config.ASR_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BusinessError("本地语音转写超时，请换小一点的 Whisper 模型后重试")
    if proc.returncode != 0:
        detail = err.decode("utf-8", "ignore")[-1200:]
        raise BusinessError(f"本地语音转写失败：{detail or '请检查 Whisper 与 FFmpeg 是否可用'}")
    try:
        result = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BusinessError("本地语音转写结果解析失败")

    cues = [
        {"start": round(float(c["start"]), 2), "end": round(float(c["end"]), 2), "text": c["text"]}
        for c in result.get("segments", []) if c.get("text")
    ]
    text = "\n".join(c["text"] for c in cues).strip()
    if not text:
        raise BusinessError("没有识别到清晰语音，请确认视频有人声")
    return {"text": text, "cues": cues}
