#!/bin/bash
D=${1:?"Usage: sudo bash setup_nginx.sh domain-or-ip"}
cat > /tmp/aitrader.nginx <<EOF
server {
    listen 80;
    server_name $D;
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; }
    location /ws { proxy_pass http://127.0.0.1:8000/ws; proxy_http_version 1.1; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade"; proxy_read_timeout 86400; }
}
EOF
sudo cp /tmp/aitrader.nginx /etc/nginx/sites-available/ai-trader
sudo ln -sf /etc/nginx/sites-available/ai-trader /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
echo "Nginx configured: http://$D"
if ! [[ "$D" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    sudo certbot --nginx -d "$D" --non-interactive --agree-tos --email admin@$D 2>/dev/null && echo "HTTPS: https://$D"
fi
