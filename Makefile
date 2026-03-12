# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

.DEFAULT_GOAL := help

.PHONY: help up down build logs shell lint migrate dev

help:
	@echo "Available targets:"
	@echo "  up       Start containers (docker compose up -d)"
	@echo "  down     Stop containers"
	@echo "  build    Rebuild image without cache"
	@echo "  logs     Tail container logs"
	@echo "  shell    Open bash shell in the app container"
	@echo "  lint     Run ruff on src/ and main.py"
	@echo "  migrate  Run alembic upgrade head inside the container"
	@echo "  dev      Run locally without Docker (requires .venv active)"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f app

shell:
	docker compose exec app bash

lint:
	ruff check src/ main.py

migrate:
	docker compose exec app python -m alembic upgrade head

dev:
	.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 5001 --reload
