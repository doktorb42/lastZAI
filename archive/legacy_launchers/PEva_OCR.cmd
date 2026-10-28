@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :path_error

rem CPU-only. This does not launch the Last Z AI model or use CUDA.
set "CUDA_VISIBLE_DEVICES=-1"
set "OMP_NUM_THREADS=2"
set "OPENBLAS_NUM_THREADS=2"
set "VE_DIR=%~dp0.peva_ocr_env"
set "VE_PY=%VE_DIR%\Scripts\python.exe"

echo ==========================================================
echo PEva - TEST OCR CPU - bez patchowania Last Z
 echo ==========================================================
if exist "%VE_PY%" goto :dependencies

echo [SETUP] Tworze osobne srodowisko Python.
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 goto :create_311
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto :create_py
python -c "import sys" >nul 2>&1
if not errorlevel 1 goto :create_python
goto :python_missing

:create_311
py -3.11 -m venv "%VE_DIR%"
if errorlevel 1 goto :venv_error
goto :dependencies

:create_py
py -3 -m venv "%VE_DIR%"
if errorlevel 1 goto :venv_error
goto :dependencies

:create_python
python -m venv "%VE_DIR%"
if errorlevel 1 goto :venv_error
goto :dependencies

:dependencies
if not exist "%VE_PY%" goto :venv_error
"%VE_PY%" -c "import rapidocr, onnxruntime, cv2, numpy" >nul 2>&1
if not errorlevel 1 goto :run_ocr

echo [SETUP] Instaluje RapidOCR i biblioteki do pracy na CPU.
"%VE_PY%" -m pip install --disable-pip-version-check rapidocr onnxruntime opencv-python-headless numpy
if errorlevel 1 goto :pip_error
"%VE_PY%" -c "import rapidocr, onnxruntime, cv2, numpy" >nul 2>&1
if errorlevel 1 goto :import_error

:run_ocr
echo [START] Uruchamiam test OCR.
"%VE_PY%" "%~dp0peva_ocr_test.py"
set "RC=%ERRORLEVEL%"
echo.
echo [KONIEC] Kod wyjscia: %RC%
pause
exit /b %RC%

:path_error
echo [BLAD] Nie mozna przejsc do katalogu ze skryptem.
goto :failed

:python_missing
echo [BLAD] Nie znaleziono Pythona 3 w systemie.
echo [WSKAZOWKA] Zainstaluj Python 3.11 z python.org.
goto :failed

:venv_error
echo [BLAD] Nie udalo sie utworzyc srodowiska .peva_ocr_env.
goto :failed

:pip_error
echo [BLAD] Nie udalo sie zainstalowac bibliotek OCR.
echo [WSKAZOWKA] Sprawdz internet i log instalatora powyzej.
goto :failed

:import_error
echo [BLAD] Biblioteki nie importuja sie po instalacji.
echo [WSKAZOWKA] Pokaz komunikaty instalacji i wersje Pythona.
goto :failed

:failed
echo.
pause
exit /b 1
