# «Орбита ФСП» — ЛЦТ 2026, спец. трек ФСП. Цели верхнего уровня (подробности: docs/08-run.md).
# Локальная разработка — make dev (docker compose); backend на хосте — make dev-infra + backend/.env.
# k8s нет намеренно: стенд — docker compose (+ Caddy на VPS, профиль prod — день 7).

COMPOSE  = docker compose -f deploy/compose/docker-compose.dev.yml
BACKEND  = backend
FRONTEND = frontend
VENV     = $(BACKEND)/.venv/bin

.PHONY: help dev dev-infra dev-full dev-env dev-down logs migrate seed demo bot \
        test lint llm-smoke load-test clean prod-deploy prod-smoke

help: ## список целей
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## --- Локальная разработка ---------------------------------------------------
dev: dev-env ## compose-стек: postgres + keycloak + mailpit + migrate + backend (hot-reload)
	$(COMPOSE) up -d --build
	@echo "API:      http://localhost:8000/api/v1/docs"
	@echo "Keycloak: http://localhost:8080  (admin / KC_ADMIN_PASSWORD из deploy/compose/.env)"
	@echo "Почта:    http://localhost:8025  (Mailpit — письма подтверждения и сброса пароля)"
	@echo "Фронт:    cd frontend && npm run dev   → http://localhost:5173"

dev-infra: dev-env ## только инфраструктура (postgres + keycloak + mailpit) — backend на хосте
	$(COMPOSE) up -d postgres keycloak mailpit

dev-full: dev-env ## compose-стек + фронт в контейнере (nginx) → http://localhost:3000
	$(COMPOSE) --profile full up -d --build
	@echo "Стенд: http://localhost:3000"

dev-env: ## сгенерировать deploy/compose/.env, если его нет (ключи ПДн, токены)
	./deploy/scripts/gen-dotenv.sh

dev-down: ## остановить compose-стек (тома сохраняются)
	$(COMPOSE) --profile full down

logs: ## логи backend (compose)
	$(COMPOSE) logs -f backend

## --- Данные -------------------------------------------------------------------
migrate: ## alembic upgrade head в контейнере backend (образ fsp-backend:dev)
	$(COMPOSE) run --rm migrate

seed: ## идемпотентный сид демо-данных (python -m app.tools.seed в контейнере backend)
	$(COMPOSE) run --rm --no-deps backend python -m app.tools.seed

demo: seed ## подготовить демо-стенд (заглушка дня 6: только сид)
	@echo "demo: пока только сид; пул заданий, демо-аккаунты и сценарий жюри — позже (docs/08-run.md)"

bot: ## телеграм-бот на хосте (режим из backend/.env: TELEGRAM_BOT_MODE=stub|polling)
	@test -x $(VENV)/python || { echo "нет backend/.venv — см. CLAUDE.md «Команды»"; exit 1; }
	cd $(BACKEND) && .venv/bin/python -m app.tools.bot

## --- Прод-стенд (VPS, Caddy) ----------------------------------------------------
PROD_COMPOSE = deploy/compose/docker-compose.prod.yml

prod-deploy: ## деплой на VPS: VM_HOST=root@ip PUBLIC_DOMAIN=orbita.<ip>.nip.io make prod-deploy
	@test -n "$(VM_HOST)" || { echo "задайте VM_HOST=user@host (docs/08-run.md §8)"; exit 1; }
	@test -n "$(PUBLIC_DOMAIN)" || { echo "задайте PUBLIC_DOMAIN=домен (например orbita.<ip>.nip.io)"; exit 1; }
	VM_HOST="$(VM_HOST)" COMPOSE_FILE=$(PROD_COMPOSE) \
	  PUBLIC_BASE=https://$(PUBLIC_DOMAIN) ./deploy/scripts/vm-deploy.sh deploy

prod-smoke: ## smoke-проверка прод-стенда (переменные — как у prod-deploy)
	@test -n "$(VM_HOST)" || { echo "задайте VM_HOST=user@host"; exit 1; }
	@test -n "$(PUBLIC_DOMAIN)" || { echo "задайте PUBLIC_DOMAIN=домен"; exit 1; }
	VM_HOST="$(VM_HOST)" COMPOSE_FILE=$(PROD_COMPOSE) \
	  PUBLIC_BASE=https://$(PUBLIC_DOMAIN) ./deploy/scripts/vm-deploy.sh smoke

## --- Качество -----------------------------------------------------------------
test: ## backend pytest (из backend/.venv) + frontend tsc
	@test -x $(VENV)/pytest || { echo "нет backend/.venv — см. CLAUDE.md «Команды»"; exit 1; }
	cd $(BACKEND) && .venv/bin/pytest -q
	@if [ -f $(FRONTEND)/package.json ]; then cd $(FRONTEND) && npx tsc --noEmit; \
	  else echo "frontend/package.json нет — tsc пропущен"; fi

lint: ## ruff check + ruff format --check (backend)
	cd $(BACKEND) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests

llm-smoke: ## smoke-тест строгого JSON LLM (app/tools/llm_smoke.py; провайдер — LLM_PROVIDER)
	cd $(BACKEND) && .venv/bin/python -m app.tools.llm_smoke

load-test: ## k6: сценарии «подборка» и «раннер теста» против локального стека (deploy/load)
	@command -v k6 >/dev/null 2>&1 || \
	  { echo "k6 не установлен — запуск в docker описан в deploy/load/README.md"; exit 1; }
	k6 run deploy/load/k6-script.js --summary-export deploy/load/summary.json

## --- Очистка ------------------------------------------------------------------
clean: ## снести compose-стек с томами; deploy/compose/.env сохраняется
	$(COMPOSE) --profile full down -v --remove-orphans 2>/dev/null || true
	@echo "Сгенерированный deploy/compose/.env сохранён (ключи ПДн стабильны)."
