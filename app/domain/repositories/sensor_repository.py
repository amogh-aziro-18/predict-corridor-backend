"""Sensor repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Sensor


class SensorRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, sensor_id: str) -> Sensor | None:
        return self._db.get(Sensor, sensor_id)

    def list_by_asset(self, asset_id: str) -> list[Sensor]:
        stmt = select(Sensor).where(Sensor.asset_id == asset_id).order_by(Sensor.id)
        return list(self._db.scalars(stmt).all())

    def get_by_asset_metric(self, asset_id: str, metric_key: str) -> Sensor | None:
        stmt = (
            select(Sensor)
            .where(Sensor.asset_id == asset_id, Sensor.metric_key == metric_key)
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def upsert(self, sensor: Sensor) -> Sensor:
        merged = self._db.merge(sensor)
        self._db.flush()
        return merged
