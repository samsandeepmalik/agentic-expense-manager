.DEFAULT_GOAL := help
.PHONY: help start stop restart cleanup logs logs-api logs-web status dev-api dev-web \
        deploy bootstrap

# --- Oracle Cloud config (override on CLI: make deploy ORACLE_IP=1.2.3.4) -----
ORACLE_IP   ?= REPLACE_WITH_IP
ORACLE_USER ?= ubuntu
ORACLE_HOST  = $(ORACLE_USER)@$(ORACLE_IP)

help: ## Show this help
	@echo "Expense Manager — available commands:"
	@echo ""
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

# --- Docker (default way to run the app) -----------------------------------

start: ## Build + start backend (API) and console (web UI) in Docker
	docker compose up -d --build
	@echo ""
	@echo "✅ Up:  Web UI  → http://localhost:5173"
	@echo "        API     → http://localhost:8000"

stop: ## Stop all containers (state is kept)
	docker compose down

restart: stop start ## Stop then start

cleanup: ## Remove containers + images. Your data in ./data is PRESERVED.
	docker compose down --rmi local
	rm -rf web/dist
	@echo ""
	@echo "🔒 Data preserved at ./data (expense.db, receipts/, whatsapp/)."
	@echo "   To wipe it too: make cleanup-data"

cleanup-data: ## DESTRUCTIVE: also delete ./data (DB + receipts + WhatsApp pairing)
	rm -rf data
	@echo "🗑  ./data removed."

logs: ## Follow logs from all services
	docker compose logs -f

logs-api: ## Follow backend (API) logs
	docker compose logs -f api

logs-web: ## Follow console (web UI) logs
	docker compose logs -f web

status: ## Show container status
	docker compose ps

# --- Local development (without Docker) ------------------------------------

dev-api: ## Run API locally with hot reload (poetry install --no-root, libmagic)
	cd api && DATA_DIR=$(PWD)/data poetry run uvicorn app.main:app --reload --port 8000

dev-web: ## Run web UI dev server locally (npm install first)
	cd web && npm run dev

# --- Remote deployment — run these FROM your Mac ---------------------------
# On the VM itself, use the same local targets: make start / stop / restart / logs

deploy: ## [Mac] Sync code to Oracle VM + restart prod stack (set ORACLE_IP=x.x.x.x)
	@if [ "$(ORACLE_IP)" = "REPLACE_WITH_IP" ]; then \
		echo "Error: set ORACLE_IP — e.g.  make deploy ORACLE_IP=1.2.3.4"; exit 1; fi
	rsync -az --delete \
		--exclude '.git' --exclude 'data/' \
		--exclude 'web/node_modules' --exclude '**/__pycache__' \
		--exclude 'api/.venv' --exclude '*.pyc' \
		. $(ORACLE_HOST):~/app/
	ssh $(ORACLE_HOST) "cd ~/app && make restart"

bootstrap: ## [Mac] Run first-time setup on Oracle VM (set ORACLE_IP=x.x.x.x)
	@if [ "$(ORACLE_IP)" = "REPLACE_WITH_IP" ]; then \
		echo "Error: set ORACLE_IP — e.g.  make bootstrap ORACLE_IP=1.2.3.4"; exit 1; fi
	ssh $(ORACLE_HOST) "bash -s" < scripts/bootstrap.sh
