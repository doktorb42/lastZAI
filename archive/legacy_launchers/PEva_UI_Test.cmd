@echo off
setlocal
cd /d "%~dp0"
title PEva - Test odczytu interfejsu Last Z
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 peva_ui_probe.py
) else (
    python peva_ui_probe.py
)
echo.
echo Nacisnij dowolny klawisz, aby zamknac okno.
pause >nul
