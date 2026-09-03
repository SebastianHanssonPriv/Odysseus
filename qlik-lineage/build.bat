@echo off
REM Builds the Qlik lineage tool into a single standalone Windows .exe.
REM Run this on a Windows machine that has Python 3 installed. The resulting
REM dist\qlik-lineage.exe runs without Python.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Building qlik-lineage.exe
echo ============================================================

REM --- 1. Locate Python ---------------------------------------------------
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python 3 was not found. Install it from https://www.python.org and re-run.
  goto :error
)

REM --- 2. Isolated build environment --------------------------------------
if not exist ".buildenv" (
  echo Creating build environment...
  %PY% -m venv .buildenv
  if errorlevel 1 goto :error
)
call ".buildenv\Scripts\activate.bat"
if errorlevel 1 goto :error

REM --- 3. Dependencies + PyInstaller --------------------------------------
echo Installing dependencies (first run takes a few minutes)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

REM --- 4. Build -----------------------------------------------------------
echo Building executable...
pyinstaller --noconfirm --clean --onefile --console ^
  --name qlik-lineage ^
  --collect-all azure.identity ^
  --collect-all azure.keyvault.secrets ^
  --collect-all websockets ^
  --hidden-import collector ^
  --hidden-import auth ^
  --hidden-import config ^
  --hidden-import rest_client ^
  --hidden-import engine_client ^
  --hidden-import lineage ^
  --hidden-import secure_input ^
  cli.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Done.  Your executable:  dist\qlik-lineage.exe
echo ============================================================
echo  Run it from a folder that holds your .env file, e.g.:
echo     qlik-lineage.exe extract --interactive
echo.
goto :end

:error
echo.
echo BUILD FAILED - see the messages above.
endlocal
exit /b 1

:end
endlocal
