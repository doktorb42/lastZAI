@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :path_error

title PEva - czuwanie wiadomosci
echo ==========================================================
echo PEva - CZUWANIE PRYWATNEGO CZATU
echo ==========================================================
echo.
echo Zatrzymanie: Ctrl+C albo zamkniecie tego okna.
echo PEva nie uruchamia modelu i nie wysyla wiadomosci.
echo.

set "PEVA_PY=%~dp0.peva_ocr_env\Scripts\python.exe"
if exist "%PEVA_PY%" goto :run

py -3 -B -c "import sys" >nul 2>&1
if errorlevel 1 goto :python_missing
set "PEVA_PY=py -3"

:run
%PEVA_PY% -B "%~dp0peva_eyes_watch.py"
set "RC=%ERRORLEVEL%"
echo.
echo Czuwanie zakonczone. Kod: %RC%
pause
exit /b %RC%

:path_error
echo [BLAD] Nie mozna przejsc do katalogu PEva.
pause
exit /b 1

:python_missing
echo [BLAD] Nie znaleziono srodowiska PEva ani Pythona 3.
echo [WSKAZOWKA] Uruchom najpierw PEva_OCR.cmd, aby przygotowac srodowisko OCR.
pause
exit /b 1
