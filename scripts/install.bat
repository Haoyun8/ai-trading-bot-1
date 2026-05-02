@echo off
chcp 65001 >nul 2>&1
echo ============================================
echo   AI Trader Pro v7.0 - Windows Installer
echo ============================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    echo.
    echo Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)

:: Check Python version
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found

:: Create virtual environment
echo.
echo [1/4] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment already exists
)

:: Activate and install dependencies
echo.
echo [2/4] Installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo [OK] Dependencies installed

:: Create directories
echo.
echo [3/4] Creating directories...
if not exist "data" mkdir data
if not exist "logs" mkdir logs
echo [OK] Directories created

:: Copy .env if needed
echo.
echo [4/4] Checking configuration...
if not exist ".env" (
    copy .env.example .env >nul
    echo [!] Created .env from .env.example
    echo [!] IMPORTANT: Edit .env with your API keys before starting!
) else (
    echo [OK] .env already exists
)

echo.
echo ============================================
echo   Installation Complete!
echo ============================================
echo.
echo Next steps:
echo   1. Edit .env with your API keys (notepad .env)
echo   2. Run: start.bat
echo   3. Open: http://localhost:8000
echo.
echo For auto-start on boot, run: install-service.bat (as Administrator)
echo.
pause
