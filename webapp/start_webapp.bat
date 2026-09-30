@echo off
rem Sync-rate explorer server launcher (Windows).
rem - Settings come from <repo>\.env (copy .env.example). Nothing secret lives in this file.
rem - If the server stops or crashes, it restarts after 10 seconds.
rem - Log: <repo>\data\webapp.log
rem Double-click to run, or register for auto start at logon with install_autostart.ps1.

cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist data mkdir data

:loop
echo [%date% %time%] start >> data\webapp.log
py -u -m webapp >> data\webapp.log 2>&1
echo [%date% %time%] stopped (exit %errorlevel%) - restart in 10s >> data\webapp.log
timeout /t 10 /nobreak >nul
goto loop
