.PHONY: install db-up db-down migrate run test lint

install:
	pip install -e ./shared -e ./backend -e ./cli

db-up:
	docker compose -f infra/docker-compose.yml up -d

db-down:
	docker compose -f infra/docker-compose.yml down

migrate:
	cd backend && alembic upgrade head

run:
	uvicorn groundline_api.main:app --reload --app-dir backend

test:
	pytest backend cli shared

lint:
	ruff check backend cli shared
