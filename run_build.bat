@echo off
cd /d "%~dp0"

echo ============================================ > build_log1.txt
echo Building pdtbuddyapp.exe (BuddyApp.spec)... >> build_log1.txt
echo ============================================ >> build_log1.txt
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm BuddyApp.spec >> build_log1.txt 2>&1
echo. >> build_log1.txt
echo BuddyApp EXIT CODE: %ERRORLEVEL% >> build_log1.txt

echo. >> build_log1.txt
echo ============================================ >> build_log1.txt
echo Building IngestAutoUpdate.exe (IngestAutoUpdate.spec)... >> build_log1.txt
echo ============================================ >> build_log1.txt
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm IngestAutoUpdate.spec >> build_log1.txt 2>&1
echo. >> build_log1.txt
echo IngestAutoUpdate EXIT CODE: %ERRORLEVEL% >> build_log1.txt

echo. >> build_log1.txt
echo BOTH BUILDS COMPLETE >> build_log1.txt
