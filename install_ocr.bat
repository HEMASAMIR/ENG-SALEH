@echo off
title Blue Square - install AUTO OCR engine
cd /d "%~dp0"
echo ========================================================
echo   Installing the AUTO OCR engine into .venv
echo ========================================================
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create it first:  py -3.10 -m venv .venv
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements_ocr.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] installation failed
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('AUTO OCR engine OK')"
pause
