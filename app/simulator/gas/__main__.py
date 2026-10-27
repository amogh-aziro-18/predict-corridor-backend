"""CLI: continuous Gas telemetry simulator.

Usage:
  python -m app.simulator.gas
  python -m app.simulator.gas --once
  python -m app.simulator.gas --mode mixed --ticks 10
  python -m app.simulator.gas --mode normal --interval 3
"""

from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.simulator.gas.asset_simulator import GasAssetSimulator, dump_sample, run_continuous
from app.simulator.gas.scenarios import GasScenarioMode
from app.simulator.publishers.mqtt_publisher import MqttPublisher

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gas utility telemetry simulator")
    parser.add_argument("--once", action="store_true", help="Run a single tick then exit")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between ticks")
    parser.add_argument("--ticks", type=int, default=None, help="Stop after N ticks")
    parser.add_argument("--limit", type=int, default=5, help="Sample rows to print with --once")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible walks")
    parser.add_argument(
        "--mode",
        type=str,
        default=GasScenarioMode.MIXED.value,
        choices=[m.value for m in GasScenarioMode],
        help="Scenario mode: normal | degrading | mixed (A/B normal, C/D degrading)",
    )
    args = parser.parse_args()

    configure_logging("INFO")
    settings = get_settings()
    mode = GasScenarioMode(args.mode)

    if args.once:
        db = SessionLocal()
        publisher: MqttPublisher | None = None
        try:
            sim = GasAssetSimulator(db, seed=args.seed, mode=mode)
            sample = dump_sample(sim.run_once_json(), limit=args.limit)
            sim = GasAssetSimulator(db, seed=args.seed, mode=mode)
            if settings.mqtt_enabled:
                publisher = MqttPublisher(settings)
                publisher.start()
                count = sim.publish_tick(publisher)
                mode_label = "MQTT"
            else:
                count = sim.persist_tick()
                mode_label = "DIRECT_DB"
            print(sample)
            print(f"{mode_label}: {count} telemetry points (scenario={mode.value})")
            logger.info("%s Gas tick complete (%s points)", mode_label, count)
        finally:
            if publisher is not None:
                publisher.stop()
            db.close()
        return

    run_continuous(
        interval_seconds=args.interval,
        max_ticks=args.ticks,
        seed=args.seed,
        mode=mode,
        settings=settings,
    )


if __name__ == "__main__":
    main()
