@echo off
cd /d "%~dp0"

REM -----------------------------------------------------------------------
REM  run_build.bat  --  production build (BUDDY_PORT=80, BUDDY_HOST=0.0.0.0)
REM
REM  PyInstaller bundles the .env file that is present at build time.
REM  To ensure the correct port is baked in we temporarily patch .env,
REM  run both PyInstaller jobs, then restore the original .env.
REM -----------------------------------------------------------------------

echo ============================================ > build_log1.txt
echo PDTBuddy Production Build >> build_log1.txt
echo BUDDY_PORT=80  BUDDY_HOST=0.0.0.0 >> build_log1.txt
echo ============================================ >> build_log1.txt

REM --- Patch .env for production port ---
.venv\Scripts\python.exe -c "import os,shutil; e='.env'; b='.env.runbuild_bak'; shutil.copy2(e,b) if os.path.exists(e) else None; lines=[l for l in (open(e).read().splitlines() if os.path.exists(e) else []) if not l.strip().startswith('BUDDY_PORT') and not l.strip().startswith('BUDDY_HOST')]; lines+=['BUDDY_PORT=80','BUDDY_HOST=0.0.0.0']; open(e,'w').write('\n'.join(lines)+'\n'); print('[run_build] .env patched: BUDDY_PORT=80 BUDDY_HOST=0.0.0.0')" >> build_log1.txt 2>&1

echo. >> build_log1.txt
echo ============================================ >> build_log1.txt
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

REM --- Restore original .env ---
.venv\Scripts\python.exe -c "import os,shutil; b='.env.runbuild_bak'; e='.env'; (shutil.copy2(b,e) or os.remove(b)) if os.path.exists(b) else os.remove(e) if os.path.exists(e) else None; print('[run_build] .env restored')" >> build_log1.txt 2>&1

echo. >> build_log1.txt
echo BOTH BUILDS COMPLETE (port 80 baked in) >> build_log1.txt
