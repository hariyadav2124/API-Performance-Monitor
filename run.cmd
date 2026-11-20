@echo off
rem Start API Sentinel from Windows CMD:  run.cmd
rem Optional arguments are passed through, e.g.  run.cmd --port 5001
setlocal
cd /d "%~dp0"

if exist "apif\Scripts\activate.bat" (
    call "apif\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [WARN] Virtual environment was not found, using the Python on PATH.
)

if "%MONITOR_TOKEN%"=="" (
    echo [WARN] MONITOR_TOKEN is not set. The dashboard will be read-only.
    echo        Set it first with:  set MONITOR_TOKEN=your-secret
    echo.
)

python app.py %*
endlocal
