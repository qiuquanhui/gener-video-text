@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PORT=8000"
set "URL=http://127.0.0.1:8000/"

rem ---------- 0) 若已在运行：直接打开浏览器 ----------
netstat -ano | findstr "LISTENING" | findstr ":%PORT% " >nul 2>nul
if not errorlevel 1 (
    echo [提示] 端口 %PORT% 已有服务在运行，直接打开浏览器。
    start "" "%URL%"
    exit /b 0
)

rem ---------- 1) 环境自检 ----------
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [警告] 未检测到 ffmpeg：视频合并与 MP3 转码将不可用。
    echo         请安装 ffmpeg 并加入 PATH：https://www.gyan.dev/ffmpeg/builds/
)

if not exist ".venv\Scripts\python.exe" (
    echo [初始化] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败：请先安装 Python 3.10+ 并加入 PATH。
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, yt_dlp" >nul 2>nul
if errorlevel 1 (
    echo [初始化] 首次运行：安装依赖 ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

rem ---------- 2) 启动（约 3 秒后自动打开浏览器） ----------
echo [启动] 闪电下载服务：%URL%
echo         关闭本窗口或按 Ctrl+C 即可停止服务。
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul && start %URL%"

".venv\Scripts\python.exe" backend\main.py

echo.
echo [退出] 服务已停止。
pause
