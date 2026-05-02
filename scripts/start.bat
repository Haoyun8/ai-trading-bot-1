@echo off
chcp 65001 >nul 2>&1
echo Starting AI Trader Pro...
call venv\Scripts\activate.bat
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
