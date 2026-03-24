@echo off
setlocal
chcp 65001 >nul
title AI Thesis Polisher Launcher

echo =======================================================
echo               AI Thesis Polisher Launcher
echo =======================================================
echo.

set "PYTHON_CMD="
python --version >nul 2>&1
if %errorlevel% equ 0 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1
    if %errorlevel% equ 0 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3 was not found.
    echo Install Python from: https://www.python.org/downloads/
    echo Make sure "Add python.exe to PATH" is enabled during installation.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt was not found.
    echo Please run this script from the project root directory.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment...
    call %PYTHON_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

set "VENV_PYTHON=venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment python was not found: %VENV_PYTHON%
    pause
    exit /b 1
)

echo [SETUP] Installing dependencies...
call "%VENV_PYTHON%" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo [RUN] Starting local web UI...
call "%VENV_PYTHON%" -m streamlit run ui\app.py

pause
