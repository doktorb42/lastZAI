@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PEVA_PY=%~dp0.peva_ocr_env\Scripts\python.exe"
if exist "%PEVA_PY%" goto :run

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -B "%~dp0main.py"
    exit /b %ERRORLEVEL%
)

python -B "%~dp0main.py"
exit /b %ERRORLEVEL%

:run
"%PEVA_PY%" -B "%~dp0main.py"
exit /b %ERRORLEVEL%
