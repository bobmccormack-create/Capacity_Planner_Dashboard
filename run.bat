@echo off
REM ============================================================
REM  Capacity Planner - one-click setup and launch
REM  Double-click this file (or run it) to set up and start
REM  the app. Safe to run again later - it just launches faster
REM  on repeat runs since setup is already done.
REM ============================================================

cd /d "%~dp0"

echo.
echo === Capacity Planner setup ===
echo Working folder: %cd%
echo.

REM --- Check Python is installed ---
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on this computer.
    echo Please install Python from https://www.python.org/downloads/
    echo During install, make sure to check "Add Python to PATH".
    pause
    exit /b 1
)

REM --- Create virtual environment if it doesn't exist yet ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists, skipping creation.
)

REM --- Install/update dependencies using the venv's own pip ---
echo.
echo Installing dependencies (this may take a minute the first time)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM --- Create .env from the example if it doesn't exist yet ---
if not exist ".env" (
    echo.
    echo No .env file found - creating one from .env.example.
    echo You can edit .env later to add your Zoho credentials.
    copy ".env.example" ".env" >nul
)

REM --- Launch the app ---
echo.
echo === Starting Capacity Planner ===
echo A browser tab should open automatically.
echo Close this window (or press Ctrl+C) to stop the app.
echo.
".venv\Scripts\python.exe" -m streamlit run main.py

pause
