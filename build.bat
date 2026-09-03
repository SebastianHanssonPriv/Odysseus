@echo off
REM ============================================================
REM  Build BufabBIGovernanceStudio.exe   (run this on Windows)
REM  Requires Python 3.9+ installed and on PATH.
REM  One exe = Qlik governance + Power BI usage, one window.
REM ============================================================

REM Always run from the folder this .bat lives in
cd /d "%~dp0"

echo Working folder: %CD%
echo.

if not exist "requirements.txt" (
    echo ERROR: requirements.txt not found in this folder.
    pause
    exit /b 1
)
if not exist "studio_app.py" (
    echo ERROR: studio_app.py not found in this folder.
    pause
    exit /b 1
)

echo Installing dependencies (first run takes a few minutes)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Building standalone executable...
set ICON_OPT=
set DATA_OPT=
if exist "app_icon.ico" (
    echo Using icon: app_icon.ico
    set ICON_OPT=--icon app_icon.ico
    set DATA_OPT=--add-data "app_icon.ico;."
) else (
    echo No app_icon.ico found - building with the default icon.
)
if exist "bufab_header.png" set DATA_OPT=%DATA_OPT% --add-data "bufab_header.png;."
set VER_OPT=
if exist "version_info.txt" (
    echo Using version info: version_info.txt
    set VER_OPT=--version-file version_info.txt
) else (
    echo No version_info.txt found - building without file details.
)

python -m PyInstaller --onefile --windowed --name BufabBIGovernanceStudio ^
  %ICON_OPT% %VER_OPT% %DATA_OPT% ^
  --collect-all azure.identity ^
  --collect-all azure.keyvault.secrets ^
  --hidden-import auth ^
  --hidden-import config ^
  --hidden-import powerbi_client ^
  --hidden-import activity_events ^
  --hidden-import raw_export ^
  --hidden-import analytics ^
  --hidden-import qlik_core ^
  --hidden-import qlik_capacity ^
  --hidden-import scanner ^
  --hidden-import dataflow_admin ^
  --hidden-import mashup_parser ^
  --hidden-import model_lineage ^
  studio_app.py
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Done!  Your program is here:
echo     dist\BufabBIGovernanceStudio.exe
echo ============================================================
echo You can double-click it - no Python needed on other PCs.
pause
exit /b 0

:error
echo.
echo Build FAILED. Check the messages above.
pause
exit /b 1
