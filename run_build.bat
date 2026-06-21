@echo off
cd /d C:\Users\slua_0038d920d430\Desktop\Buddy
echo Starting PyInstaller build... > build_log.txt
echo. >> build_log.txt
venv\Scripts\python.exe -m PyInstaller --clean --noconfirm BuddyApp.spec >> build_log.txt 2>&1
echo. >> build_log.txt
echo EXIT CODE: %ERRORLEVEL% >> build_log.txt
echo BUILD COMPLETE >> build_log.txt
