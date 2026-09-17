@echo off
REM ============================================================
REM  便签客户端 - Windows 启动脚本（首次自动创建 venv 并装依赖）
REM  用法：双击本文件，或用 cmd 执行 run_windows.bat
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python 启动器 py。请先安装 Python 3.10~3.12 并勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

if not exist venv (
    echo [首次运行] 正在创建虚拟环境 venv ...
    py -3 -m venv venv
)

echo [安装/校准依赖] requirements.txt ...
venv\Scripts\python -m pip install -q -r requirements.txt

echo [启动] 便签客户端（后台窗口）...
start "" venv\Scripts\pythonw.exe app.py

endlocal
