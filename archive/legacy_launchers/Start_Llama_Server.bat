@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start_Llama_Server.ps1"
if errorlevel 1 (
  echo.
  echo Serwer zakonczyl dzialanie lub wystapil blad.
)
pause
