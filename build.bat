@echo off
REM Builds the Odysseus governance toolkit into a single standalone Windows
REM .exe covering both platforms. Run this on a Windows machine that has
REM Python 3 installed. The resulting dist\odysseus.exe runs without Python.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Building odysseus.exe
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
  --name odysseus ^
  --collect-all azure.identity ^
  --collect-all azure.keyvault.secrets ^
  --collect-all websockets ^
  --hidden-import secure_input ^
  --hidden-import powerbi ^
  --hidden-import powerbi.collector ^
  --hidden-import powerbi.auth ^
  --hidden-import powerbi.config ^
  --hidden-import powerbi.powerbi_client ^
  --hidden-import powerbi.activity_events ^
  --hidden-import powerbi.raw_export ^
  --hidden-import powerbi.analytics ^
  --hidden-import qlik ^
  --hidden-import qlik.collector ^
  --hidden-import qlik.auth ^
  --hidden-import qlik.config ^
  --hidden-import qlik.rest_client ^
  --hidden-import qlik.engine_client ^
  --hidden-import qlik.lineage ^
  --hidden-import qlik.qvd_lineage ^
  --hidden-import qlik.script_parser ^
  cli.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Done.  Your executable:  dist\odysseus.exe
echo ============================================================
echo  Run it from a folder that holds your .env file, e.g.:
echo     odysseus.exe powerbi collect --interactive
echo     odysseus.exe powerbi raw-export
echo     odysseus.exe powerbi analytics
echo     odysseus.exe qlik extract --interactive
echo.
goto :end

:error
echo.
echo BUILD FAILED - see the messages above.
endlocal
exit /b 1

:end
endlocal
