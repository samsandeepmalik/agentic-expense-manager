.DEFAULT_GOAL := help
.PHONY: help start stop restart cleanup logs logs-api logs-web status dev-api dev-web

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

cleanup: ## Remove containers, volumes (Google/WhatsApp sessions!) and images
	docker compose down -v --rmi local
	rm -rf api/data web/dist

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
	cd api && poetry run uvicorn app.main:app --reload --port 8000

dev-web: ## Run web UI dev server locally (npm install first)
	cd web && npm run dev
