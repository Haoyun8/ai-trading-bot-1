#!/bin/bash
set -e
echo "=== AI Trader Pro v7.0 Installation ==="
sudo apt update -qq && sudo apt install -y -qq python3 python3-pip python3-venv git curl nginx ufw > /dev/null 2>&1
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
[ ! -d "venv" ] && python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q && pip install -r requirements.txt -q
[ ! -f ".env" ] && cp .env.example .env && echo ">>> Please edit .env with your API keys <<<"
mkdir -p data logs
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable > /dev/null 2>&1
sudo cp "$DIR/systemd/ai-trader.service" /etc/systemd/system/
sudo sed -i "s|__DIR__|$DIR|g; s|__USER__|$USER|g" /etc/systemd/system/ai-trader.service
sudo systemctl daemon-reload && sudo systemctl enable ai-trader > /dev/null 2>&1
echo "=== Installation Complete ==="
echo "1. nano .env (set API keys)"
echo "2. make start (foreground) or make daemon (background)"
echo "3. Access http://localhost:8000"
