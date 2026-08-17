# Compose stack for local Postgres (+ optional HiveMQ).
#
#   docker compose -f docker/docker-compose.yml up -d postgres
#   docker compose -f docker/docker-compose.yml --profile mqtt up -d
#
# Sprint 2 APIs require Postgres + alembic upgrade head + python -m app.db.seed
