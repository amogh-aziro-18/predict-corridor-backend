# Predict Corridor Backend

Super Utility Intelligence Platform — FastAPI backend.

## Stack

- Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL · Alembic · MQTT (HiveMQ)

## Quick start

```bash
cd predict-corridor-backend
python -m venv .venv
# Windows
.venv\Scripts\activate

pip install -e .
copy .env.example .env

# Start Postgres (Docker)
docker compose -f docker/docker-compose.yml up -d postgres

# Migrate + seed Water reference data
alembic upgrade head
python -m app.db.seed

uvicorn app.main:app --reload
```

Health: http://127.0.0.1:8000/api/health

### Sprint 2 APIs

- `GET /api/utilities`
- `GET /api/zones`
- `GET /api/assets`
- `GET /api/assets/{asset_id}`
- `GET /api/assets?zone=A`
- `GET /api/assets?utility=water`
- `GET /api/corridor`

### Simulator (Sprint 3)

```bash
# Continuous telemetry (direct DB when MQTT_ENABLED=false)
python -m app.simulator.water --interval 5

# Single tick
python -m app.simulator.water --once

# Limited run
python -m app.simulator.water --ticks 20 --interval 3
```

Verify:

```bash
curl http://127.0.0.1:8001/api/telemetry/latest
curl http://127.0.0.1:8001/api/dashboard/water
```

## Notes

- MQTT is **off** by default (`MQTT_ENABLED=false`).
- Water is the first utility plugin; no water-specific DB tables (attrs live in `assets.profile` JSONB).
- Agents / LangGraph / Langfuse remain reserved stubs only.
