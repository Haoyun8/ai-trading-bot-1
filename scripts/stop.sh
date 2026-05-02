#!/bin/bash
sudo systemctl stop ai-trader 2>/dev/null
pkill -f "uvicorn app.main" 2>/dev/null
echo "AI Trader stopped"
