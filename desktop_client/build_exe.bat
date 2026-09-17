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

venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile ^
  --name StickyNotes ^
  app.py

echo.
echo ============================================================
echo  打包完成：dist\StickyNotes.exe
echo  - 首次运行若被 SmartScreen 提示，点"更多信息 - 仍要运行"
echo  - 配置后端地址：%USERPROFILE%\.config\sticky_notes\config.json 的 base_url
echo    （注意：config.json 默认不存在时客户端会连 http://127.0.0.1:8000）
echo ============================================================
endlocal
