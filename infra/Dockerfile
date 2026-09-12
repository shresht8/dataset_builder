# API image (GL-1-11). Build from the repo root so both packages are in context:
#   docker build -f infra/Dockerfile .
# (infra/docker-compose.yml does this via `build: context: ..`)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# shared/ first: backend depends on groundline-schema by name, and installing
# shared beforehand satisfies that dependency without hitting PyPI for it.
COPY shared/ shared/
COPY backend/ backend/
RUN pip install ./shared ./backend

# alembic.ini + migrations/ live in backend/; the DB URL comes from DATABASE_URL
# (read by app settings, which migrations/env.py reuses).
WORKDIR /app/backend

EXPOSE 8000

# Apply migrations, then serve. Idempotent: `alembic upgrade head` is a no-op
# on an already-migrated database. (migrations/env.py doesn't configure logging,
# so the echos are the visible evidence in `docker logs` that alembic ran.)
CMD ["sh", "-c", "echo '[entrypoint] alembic upgrade head' && alembic upgrade head && echo \"[entrypoint] migrations at head: $(alembic current)\" && exec uvicorn groundline_api.main:app --host 0.0.0.0 --port 8000"]
