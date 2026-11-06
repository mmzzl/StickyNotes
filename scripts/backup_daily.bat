@echo off
rem Silent daily backup entry for Windows Task Scheduler.
rem CLI writes its own data/backups/backup.log, so pythonw (no console) is fine.
setlocal
cd /d "%~dp0.."

rem Prefer the interpreter this project actually uses (has pydantic-settings etc).
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
  "%PYW%" scripts\backup_db.py --once
  goto done
)

where pyw.exe >nul 2>&1
if %errorlevel%==0 (
  pyw.exe -3 scripts\backup_db.py --once
  goto done
)

python scripts\backup_db.py --once

:done
endlocal
