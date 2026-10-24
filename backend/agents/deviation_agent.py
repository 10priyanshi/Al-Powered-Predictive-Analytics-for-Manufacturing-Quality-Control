"""
Deviation Prediction Agent
============================
Instead of the Prediction Agent's "is this reading already anomalous"
framing, this agent asks a forward-looking question: given the recent
TREND on a sensor, when - if ever - will it cross the normal operating
boundary, and how confident are we in that trend continuing?

Method: for each machine/sensor_type, fits a simple linear regression
(least-squares) to the last `lookback_minutes` of readings. If the
fitted line is heading toward (not away from) the nearer boundary of
the documented normal range, extrapolates the number of minutes until
it would cross that boundary. The fit's R^2 (how well a straight line
explains the recent readings) becomes the disclosed confidence/
probability figure - a noisy, inconsistent trend produces a low
probability rather than a confident-sounding guess.

This is deliberately still "just" linear regression, not a black-box
forecasting model - every predicted deviation includes the exact slope,
R^2, and reading count it was computed from, consistent with the rest
of the app's explainability requirement.
"""
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from backend.models.sensor import SensorReading
from backend.models.machine import Machine
from backend.data_generator.config import SENSOR_NORMAL_RANGES, SENSOR_UNITS

SENSOR_TYPES = ["temperature", "pressure", "vibration", "humidity"]
LOOKBACK_MINUTES = 180  # wide enough to reliably capture >= MIN_POINTS readings given
                        # the ~20-minute sensor sampling interval used elsewhere in this app
LOOKAHEAD_MINUTES = 600  # 10h - the synthetic drift scenarios are subtle over a long window,
                         # so a short lookahead (e.g. 2h) misses genuine slow trends entirely
MIN_POINTS = 6
MIN_R2_TO_REPORT = 0.35


def _fit_trend(times: np.ndarray, values: np.ndarray) -> tuple[float, float, float]:
    """Returns (slope, intercept, r_squared) for a least-squares linear fit."""
    slope, intercept = np.polyfit(times, values, 1)
    predicted = slope * times + intercept
    ss_res = float(np.sum((values - predicted) ** 2))
    ss_tot = float(np.sum((values - np.mean(values)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
    return float(slope), float(intercept), max(0.0, r_squared)


def _predict_sensor_deviation(db: Session, machine: Machine, sensor_type: str, now: datetime) -> Optional[dict]:
    window_start = now - timedelta(minutes=LOOKBACK_MINUTES)
    readings = (
        db.query(SensorReading)
        .filter(
            SensorReading.machine_id == machine.machine_id, SensorReading.sensor_type == sensor_type,
            SensorReading.timestamp >= window_start, SensorReading.timestamp <= now,
            SensorReading.value.isnot(None),
        )
        .order_by(SensorReading.timestamp.asc())
        .all()
    )
    if len(readings) < MIN_POINTS:
        return None

    times = np.array([(r.timestamp - readings[0].timestamp).total_seconds() / 60.0 for r in readings])
    values = np.array([r.value for r in readings])
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    current_value = values[-1]
    current_time = times[-1]

    if current_value < lo or current_value > hi:
        return None  # already out of range - that's a live anomaly, not a *prediction*

    slope, intercept, r_squared = _fit_trend(times, values)
    if r_squared < MIN_R2_TO_REPORT or abs(slope) < 1e-6:
        return None  # trend too noisy/flat to extrapolate meaningfully

    if slope > 0:
        target_bound, bound_name = hi, "upper"
    else:
        target_bound, bound_name = lo, "lower"

    minutes_to_cross = (target_bound - current_value) / slope
    if minutes_to_cross <= 0 or minutes_to_cross > LOOKAHEAD_MINUTES:
        return None

    probability = round(float(np.clip(r_squared * 100, 30, 97)), 1)
    if minutes_to_cross < 15:
        severity = "critical"
    elif minutes_to_cross < 60:
        severity = "warning"
    else:
        severity = "info"

    return {
        "machine_id": machine.machine_id,
        "sensor_type": sensor_type,
        "deviation_type": f"{sensor_type.capitalize()} instability - trending toward {bound_name} limit",
        "probability": probability,
        "expected_in_minutes": round(minutes_to_cross, 1),
        "severity": severity,
        "current_value": round(float(current_value), 2),
        "unit": SENSOR_UNITS[sensor_type],
        "normal_range": f"{lo}-{hi}",
        "based_on": (
            f"linear trend fit to {len(readings)} readings over the last {round(current_time)} min "
            f"(slope {slope:+.3f} {SENSOR_UNITS[sensor_type]}/min, R2={r_squared:.2f})"
        ),
    }


def predict_deviations_for_machine(db: Session, machine: Machine, now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.utcnow()
    predictions = []
    for sensor_type in SENSOR_TYPES:
        result = _predict_sensor_deviation(db, machine, sensor_type, now)
        if result:
            predictions.append(result)
    return predictions


def predict_deviations_plant_wide(db: Session, allowed_line: Optional[str] = None, limit: int = 20) -> list[dict]:
    query = db.query(Machine)
    if allowed_line:
        query = query.filter(Machine.machine_id.like(f"{allowed_line}-%"))
    machines = query.all()
    now = datetime.utcnow()
    all_predictions = []
    for machine in machines:
        all_predictions.extend(predict_deviations_for_machine(db, machine, now))
    all_predictions.sort(key=lambda p: p["expected_in_minutes"])
    return all_predictions[:limit]


def simulate_imminent_deviation(db: Session, machine: Machine, sensor_type: str) -> dict:
    """
    Deterministically injects a short run of readings with a steep,
    consistent trend heading toward one of the sensor's normal-range
    boundaries, timed so it will be predicted to cross in ~8-10 minutes
    - comfortably inside the "critical" severity band (<15 min).

    This exists because the organic synthetic data's drift scenarios are
    deliberately subtle (they play out over a 14-day window), so on
    their own they almost never produce a genuinely imminent (critical
    or warning) prediction - only slow, hours-away ones. Rather than
    loosening the severity thresholds to manufacture false urgency from
    slow trends, this gives a reliable, on-demand way to demo what an
    actually-imminent prediction looks like - the exact same real
    trend-fit math runs on this data as on anything else.

    Any pre-existing readings for this machine/sensor within the
    trend-fit's lookback window are cleared first - otherwise the old,
    noisy baseline readings still sitting in that window would dilute
    the clean trend's R^2 and the fit would fail to qualify at all.
    """
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    mid = (lo + hi) / 2
    now = datetime.utcnow()
    n_points = 8
    step_minutes = 2.5  # readings spaced 2.5 min apart, spanning the last 17.5 min

    window_start = now - timedelta(minutes=LOOKBACK_MINUTES)
    db.query(SensorReading).filter(
        SensorReading.machine_id == machine.machine_id, SensorReading.sensor_type == sensor_type,
        SensorReading.timestamp >= window_start, SensorReading.timestamp <= now,
    ).delete(synchronize_session=False)

    # Head toward whichever boundary is closer to a "just inside normal" starting point.
    target_bound = hi
    start_value = mid + (hi - mid) * 0.15  # start comfortably inside range, near the middle
    total_span_minutes = (n_points - 1) * step_minutes
    target_time_to_cross = 9.0  # minutes past the last reading
    slope = (target_bound - start_value) / (total_span_minutes + target_time_to_cross)

    inserted = []
    for i in range(n_points):
        t = now - timedelta(minutes=(n_points - 1 - i) * step_minutes)
        value = round(start_value + slope * (i * step_minutes), 2)
        reading = SensorReading(
            machine_id=machine.machine_id, sensor_type=sensor_type, value=value,
            unit=SENSOR_UNITS[sensor_type], timestamp=t, is_anomalous=False,
            data_quality_flag="ok", scenario_tag="simulated_imminent_deviation",
        )
        db.add(reading)
        inserted.append(reading)
    db.commit()

    result = _predict_sensor_deviation(db, machine, sensor_type, now)
    return {
        "machine_id": machine.machine_id, "sensor_type": sensor_type,
        "readings_inserted": len(inserted), "prediction": result,
    }
