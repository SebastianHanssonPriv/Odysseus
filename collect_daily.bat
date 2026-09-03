@echo off
REM ============================================================
REM  Daily Power BI activity collector (unattended).
REM  Collects YESTERDAY's UTC activity events and appends them to
REM  the dataset, so usage history accumulates without anyone
REM  clicking in the app.
REM
REM  SETUP (once):
REM    1. Copy .env.example to .env and fill it in. Key Vault or
REM       managed identity is recommended for an unattended task
REM       (a typed-in GUI secret can't be used here).
REM    2. Set OUTPUT_DIR in .env to your app's
REM       <Output folder>\powerbi_data so the in-app dashboard
REM       sees these collections too.
REM    3. Schedule this file once a day with Windows Task Scheduler
REM       (see HOW_TO_RUN.md -> "Run it daily").
REM  Requires Python 3.9+ on PATH and `pip install -r requirements.txt`.
REM ============================================================
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

REM collect defaults to yesterday (UTC); pass --date YYYY-MM-DD to override.
%PY% cli.py collect %*
exit /b %errorlevel%
