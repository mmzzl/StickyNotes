@echo off
REM ============================================================
REM  便签客户端 - Windows 打包脚本（PyInstaller -> 免安装单文件 EXE）
REM  用法：cmd 执行 build_exe.bat
REM  产物：dist\StickyNotes.exe ，拷给同事直接双击运行（无需装 Python）
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
    py -3 -m venv venv
)

echo [安装依赖 + PyInstaller] ...
venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller

REM 必须走 StickyNotes.spec 而不是裸跑 app.py：只有 spec 里有
REM   datas=[('vendor','vendor')]（排版视图用的 marked/turndown JS）
REM   和 QtWebEngine 的 hiddenimports。直接 pyinstaller app.py 会漏掉
REM   vendor，打包出来的 exe 点「排版」时 _read_vendor 抛
REM   FileNotFoundError，排版视图完全开不出来（Windows 特有，Linux 不受影响）。
venv\Scripts\pyinstaller --noconfirm --clean StickyNotes.spec

echo.
echo ============================================================
echo  打包完成：dist\StickyNotes.exe
echo  - 首次运行若被 SmartScreen 提示，点"更多信息 - 仍要运行"
echo  - 配置后端地址：%USERPROFILE%\.config\sticky_notes\config.json 的 base_url
echo    （注意：config.json 默认不存在时客户端会连 http://127.0.0.1:8000）
echo ============================================================
endlocal
