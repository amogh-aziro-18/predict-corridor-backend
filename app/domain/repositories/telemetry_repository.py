"""Telemetry repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.models import Telemetry


class TelemetryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, row: Telemetry) -> Telemetry:
        self._db.add(row)
        self._db.flush()
        return row

    def list_by_sensor(self, sensor_id: str, *, limit: int = 100) -> list[Telemetry]:
        stmt = (
            select(Telemetry)
            .where(Telemetry.sensor_id == sensor_id)
            .order_by(Telemetry.recorded_at.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def latest_for_sensor(self, sensor_id: str) -> Telemetry | None:
        stmt = (
            select(Telemetry)
            .where(Telemetry.sensor_id == sensor_id)
            .order_by(Telemetry.recorded_at.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def list_since(
        self,
        *,
        sensor_id: str,
        since: datetime,
        limit: int = 500,
    ) -> list[Telemetry]:
        stmt = (
            select(Telemetry)
            .where(Telemetry.sensor_id == sensor_id, Telemetry.recorded_at >= since)
            .order_by(Telemetry.recorded_at.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def history_rows(
        self,
        *,
        asset_id: str | None = None,
        asset_ids: list[str] | None = None,
        metric_key: str | None = None,
        metric_keys: list[str] | None = None,
        utility_kind: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """
        Chronological telemetry history from PostgreSQL.

        Returns dict rows suitable for DataFrame construction / API serialization.
        Ordered by ``recorded_at`` ascending.
        """
        limit = max(1, min(int(limit), 100_000))
        clauses: list[str] = []
        params: dict = {"limit": limit}

        if asset_id:
            clauses.append("s.asset_id = :asset_id")
            params["asset_id"] = asset_id
        elif asset_ids:
            placeholders = []
            for idx, aid in enumerate(asset_ids):
                key = f"asset_id_{idx}"
                placeholders.append(f":{key}")
                params[key] = aid
            clauses.append(f"s.asset_id IN ({', '.join(placeholders)})")

        if metric_key:
            clauses.append("t.metric_key = :metric_key")
            params["metric_key"] = metric_key
        elif metric_keys:
            placeholders = []
            for idx, key_name in enumerate(metric_keys):
                key = f"metric_key_{idx}"
                placeholders.append(f":{key}")
                params[key] = key_name
            clauses.append(f"t.metric_key IN ({', '.join(placeholders)})")

        if utility_kind:
            clauses.append("a.utility_kind = :utility_kind")
            params["utility_kind"] = utility_kind

        if start is not None:
            clauses.append("t.recorded_at >= :start")
            params["start"] = start
        if end is not None:
            clauses.append("t.recorded_at <= :end")
            params["end"] = end

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # Take the NEWEST ``limit`` rows, then return them chronological (ASC).
        # ASC+LIMIT alone would return the OLDEST window and drop live telemetry
        # once history exceeds the limit — breaking ML inference freshness.
        sql = text(
            f"""
            WITH newest AS (
                SELECT t.id,
                       t.sensor_id,
                       s.asset_id,
                       a.name AS asset_name,
                       t.metric_key,
                       t.value,
                       t.unit,
                       t.quality,
                       t.recorded_at
                FROM telemetry t
                JOIN sensors s ON s.id = t.sensor_id
                JOIN assets a ON a.id = s.asset_id
                {where_sql}
                ORDER BY t.recorded_at DESC
                LIMIT :limit
            )
            SELECT id, sensor_id, asset_id, asset_name, metric_key, value, unit, quality, recorded_at
            FROM newest
            ORDER BY recorded_at ASC
            """
        )
        rows = self._db.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def latest_rows(
        self,
        *,
        utility_kind: str | None = None,
    ) -> list[tuple[str, str, Telemetry]]:
        """
        Latest telemetry per (asset_id, metric_key).

        Returns list of ``(asset_id, asset_name, telemetry)``.
        Uses PostgreSQL DISTINCT ON.
        """
        if utility_kind:
            sql = text(
                """
                SELECT DISTINCT ON (s.asset_id, t.metric_key)
                       s.asset_id,
                       a.name AS asset_name,
                       t.id,
                       t.sensor_id,
                       t.metric_key,
                       t.value,
                       t.unit,
                       t.quality,
                       t.recorded_at
                FROM telemetry t
                JOIN sensors s ON s.id = t.sensor_id
                JOIN assets a ON a.id = s.asset_id
                WHERE a.utility_kind = :utility_kind
                ORDER BY s.asset_id, t.metric_key, t.recorded_at DESC
                """
            )
            rows = self._db.execute(sql, {"utility_kind": utility_kind}).mappings().all()
        else:
            sql = text(
                """
                SELECT DISTINCT ON (s.asset_id, t.metric_key)
                       s.asset_id,
                       a.name AS asset_name,
                       t.id,
                       t.sensor_id,
                       t.metric_key,
                       t.value,
                       t.unit,
                       t.quality,
                       t.recorded_at
                FROM telemetry t
                JOIN sensors s ON s.id = t.sensor_id
                JOIN assets a ON a.id = s.asset_id
                ORDER BY s.asset_id, t.metric_key, t.recorded_at DESC
                """
            )
            rows = self._db.execute(sql).mappings().all()

        result: list[tuple[str, str, Telemetry]] = []
        for row in rows:
            tel = Telemetry(
                id=row["id"],
                sensor_id=row["sensor_id"],
                metric_key=row["metric_key"],
                value=row["value"],
                unit=row["unit"],
                quality=row["quality"],
                recorded_at=row["recorded_at"],
            )
            result.append((row["asset_id"], row["asset_name"], tel))
        return result
