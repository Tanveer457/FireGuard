@echo off
set VENV_PATH=%~dp0edge\edge_venv\Scripts\pythonw.exe
set LOG_FILE=%~dp0edge\background_error.log

echo Starting Dedicated FireGuard Edge Service...
echo Using Dedicated Edge Venv: %VENV_PATH%

:: Check if venv exists
if not exist "%VENV_PATH%" (
    echo ERROR: Dedicated environment not found at %VENV_PATH%
    echo Please run: python -m venv edge_venv
    pause
    exit /b
)

cd /d "%~dp0edge"
:: Run with the venv pythonw and log errors to a file
start /B "" "%VENV_PATH%" main.py --server-url ws://127.0.0.1:8000/ws/edge > background_out.log 2> "%LOG_FILE%"

echo Done! Check your Windows App.
echo If it still doesn't connect, check: edge\background_error.log
timeout /t 5
