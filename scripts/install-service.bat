@echo off
chcp 65001 >nul 2>&1
echo ============================================
echo   Windows Service Installer (Task Scheduler)
echo ============================================
echo.
echo This will create a scheduled task to start AI Trader on system boot.
echo Run this script as Administrator!
echo.

:: Get current directory
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"

:: Create start script for service
(
echo @echo off
echo cd /d "%DIR%"
echo call venv\Scripts\activate.bat
echo python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
) > "%DIR%\service-start.bat"

:: Create scheduled task
schtasks /create /tn "AITraderPro" /tr "%DIR%\service-start.bat" /sc onstart /ru SYSTEM /rl HIGHEST /f
if %errorlevel% equ 0 (
    echo [OK] Scheduled task "AITraderPro" created
    echo     System will start AI Trader on boot
    echo.
    echo To start now:     schtasks /run /tn "AITraderPro"
    echo To stop:          schtasks /end /tn "AITraderPro"
    echo To remove:        schtasks /delete /tn "AITraderPro" /f
) else (
    echo [ERROR] Failed to create task. Run as Administrator!
)
echo.
pause
