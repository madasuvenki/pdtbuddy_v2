@echo off
cd /d C:\Users\slua_0038d920d430\Desktop\PDT_Buddy_V3.1

echo ============================================ > build_log.txt
echo Building pdtbuddyapp.exe (BuddyApp.spec)... >> build_log.txt
echo ============================================ >> build_log.txt
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm BuddyApp.spec >> build_log.txt 2>&1
echo. >> build_log.txt
echo BuddyApp EXIT CODE: %ERRORLEVEL% >> build_log.txt

echo. >> build_log.txt
echo ============================================ >> build_log.txt
echo Building IngestAutoUpdate.exe (IngestAutoUpdate.spec)... >> build_log.txt
echo ============================================ >> build_log.txt
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm IngestAutoUpdate.spec >> build_log.txt 2>&1
echo. >> build_log.txt
echo IngestAutoUpdate EXIT CODE: %ERRORLEVEL% >> build_log.txt

echo. >> build_log.txt
echo BOTH BUILDS COMPLETE >> build_log.txt
