.PHONY: install start stop restart daemon logs nginx status

install:
	bash scripts/install.sh

start:
	bash scripts/start.sh

stop:
	bash scripts/stop.sh

restart:
	bash scripts/stop.sh && sleep 1 && bash scripts/start.sh

daemon:
	sudo systemctl restart ai-trader
	sudo systemctl status ai-trader --no-pager

logs:
	tail -f logs/ai-trader.log

nginx:
	@bash scripts/setup_nginx.sh $(DOMAIN)

status:
	@curl -s http://localhost:8000/api/status 2>/dev/null | python3 -m json.tool || echo "Service not running"
