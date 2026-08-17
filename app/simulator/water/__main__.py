"""CLI: continuous Water telemetry simulator.

Usage:
  python -m app.simulator.water                 # continuous, direct DB persist
  python -m app.simulator.water --once          # single tick
  python -m app.simulator.water --interval 3
  python -m app.simulator.water --ticks 10
"""

from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.simulator.publishers.mqtt_publisher import MqttPublisher
from app.simulator.water.asset_simulator import WaterAssetSimulator, dump_sample, run_continuous

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Water utility telemetry simulator")
    parser.add_argument("--once", action="store_true", help="Run a single tick then exit")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between ticks")
    parser.add_argument("--ticks", type=int, default=None, help="Stop after N ticks")
    parser.add_argument("--limit", type=int, default=5, help="Sample rows to print with --once")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible walks")
    args = parser.parse_args()

    configure_logging("INFO")
    settings = get_settings()

    if args.once:
        db = SessionLocal()
        publisher: MqttPublisher | None = None
        try:
            sim = WaterAssetSimulator(db, seed=args.seed)
            sample = dump_sample(sim.run_once_json(), limit=args.limit)
            # Reset generator state by recreating simulator for the real write tick.
            sim = WaterAssetSimulator(db, seed=args.seed)
            if settings.mqtt_enabled:
                publisher = MqttPublisher(settings)
                publisher.start()
                count = sim.publish_tick(publisher)
                mode = "MQTT"
            else:
                count = sim.persist_tick()
                mode = "DIRECT_DB"
            print(sample)
            print(f"{mode}: {count} telemetry points")
            logger.info("%s tick complete (%s points)", mode, count)
        finally:
            if publisher is not None:
                publisher.stop()
            db.close()
        return

    run_continuous(
        interval_seconds=args.interval,
        max_ticks=args.ticks,
        seed=args.seed,
        settings=settings,
    )


if __name__ == "__main__":
    main()
