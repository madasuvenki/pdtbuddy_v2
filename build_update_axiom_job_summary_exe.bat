@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo ERROR: venv\Scripts\python.exe not found.
  exit /b 1
)

if not exist ".env" (
  echo WARNING: .env not found. The EXE will still build, but credentials/DB config must be provided externally.
)

echo Building UpdateAxiomJobSummary.exe ...
"venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm UpdateAxiomJobSummary.spec
if errorlevel 1 exit /b %errorlevel%

echo.
echo Build complete: dist\UpdateAxiomJobSummary.exe
echo.
echo Example:
echo   dist\UpdateAxiomJobSummary.exe --refresh-qipl-last-days --qipl-days 10
echo.
endlocal
