@echo off
REM 打包 pywebview 版便签客户端（Windows）
REM
REM 前提：
REM   1. 目标机器需有 WebView2 Runtime（Win10/11 通常已预装；
REM      未装时从微软官网下 Evergreen Standalone Installer）
REM   2. pip install -r requirements.txt pyinstaller
REM
REM 与旧版 PyQt5 打包的关键差异：不再需要 PyQtWebEngine，
REM 也不再往产物里塞 Chromium，体积从 200MB+ 降到几十 MB。

setlocal
cd /d "%~dp0"

echo [1/2] 安装依赖...
@REM pip install -r requirements.txt pyinstaller || goto :err

echo [2/2] 打包...
pyinstaller --noconfirm --clean StickyNotesWeb.spec || goto :err

echo.
echo 完成：dist\StickyNotesWeb.exe
dir /b dist
exit /b 0

:err
echo 打包失败，请检查上方报错。
exit /b 1
