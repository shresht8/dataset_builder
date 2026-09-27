.PHONY: install db-up db-down migrate run test lint web-install web-dev web-build web-lint

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

# backend/tests and cli/tests are both packages named `tests`, so one pytest
# run can't collect them together; run each suite on its own. (shared/ has no
# tests; its JSON Schema tests live in backend/tests/test_jsonschema.py.)
test:
	pytest backend
	pytest cli

lint:
	ruff check backend cli shared

web-install:
	cd web && npm install

web-dev:
	cd web && npm run dev

web-build:
	cd web && npm run build

web-lint:
	cd web && npm run lint
