"""
Data freshness / catch-up
============================
The synthetic data generator produces a fixed 14-day history ending at
the moment it was run. Every "live window" agent (the Prediction Agent,
the Deviation Prediction Agent) looks back a few hours from the actual
current wall-clock time - so if the server is started well after the
database was generated (minutes to hours later, e.g. after downloading
and unzipping the project), that window can end up completely empty:
not because nothing needs attention, but because there's no data there
at all to compute from.

This module closes that gap on server startup: if the newest sensor
reading is older than CATCH_UP_THRESHOLD_MINUTES, it backfills plain
baseline readings (same generation logic the data generator's normal-
noise baseline uses, tagged distinctly so they're identifiable) from
the last real reading up to now, for every machine/sensor type. This
keeps the live-window agents populated without requiring the user to
remember to regenerate the database before every session.

This intentionally does NOT try to replay the original defect-injection
scenarios (drift/spikes/etc) - it just keeps the clock "topped up" with
plausible normal readings so live queries have something recent to work
with. Anomalies for a fresh demo are still introduced deliberately via
the "Simulate incoming data" button, which is unaffected by this.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.models.sensor import SensorReading
from backend.models.machine import Machine
from backend.data_generator.defect_scenarios import generate_baseline_series

CATCH_UP_THRESHOLD_MINUTES = 30
SAMPLE_INTERVAL_MINUTES = 20
SENSOR_TYPES = ["temperature", "pressure", "vibration", "humidity"]


def catch_up_sensor_data(db: Session) -> int:
    """Returns the number of backfilled readings inserted (0 if the data was already fresh)."""
    latest = db.query(SensorReading).order_by(SensorReading.timestamp.desc()).first()
    if latest is None:
        return 0  # no data at all yet - the data generator hasn't been run; nothing to catch up

    now = datetime.utcnow()
    gap_minutes = (now - latest.timestamp).total_seconds() / 60
    if gap_minutes < CATCH_UP_THRESHOLD_MINUTES:
        return 0  # still fresh enough - the normal generated history covers the live window

    machines = db.query(Machine).all()
    rng = random.Random()
    start = latest.timestamp + timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    inserted = 0
    for machine in machines:
        for sensor_type in SENSOR_TYPES:
            series = generate_baseline_series(machine.machine_id, sensor_type, start, now, SAMPLE_INTERVAL_MINUTES, rng)
            for reading in series:
                reading["scenario_tag"] = "startup_catchup"
                db.add(SensorReading(**reading))
                inserted += 1
    db.commit()
    return inserted
