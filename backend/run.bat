@echo off
REM Convenience launcher for the algo trading backend on Windows.
REM Creates a venv on first run, installs deps, then starts main.py which
REM runs the trading engine and the FastAPI server in the same event loop.

setlocal
cd /d "%~dp0"

if not exist .venv (
    echo [run.bat] Creating virtual environment in .venv ...
    python -m venv .venv || goto :error
    call .venv\Scripts\activate.bat
    echo [run.bat] Installing dependencies ...
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt || goto :error
) else (
    call .venv\Scripts\activate.bat
)

if not exist .env (
    echo [run.bat] No .env found. Copy .env.example to .env and set BINANCE_API_KEY / SECRET ^(other knobs default in common/config.py^).
    goto :error
)

python main.py %*
goto :eof

:error
echo [run.bat] Failed. See messages above.
exit /b 1
