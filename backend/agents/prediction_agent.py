"""
Prediction Agent
=================
Scores each machine/line with a continuous defect-risk score (0-100%).

Design choice (per spec: favor explainability over black-box complexity):
  - A RULE-BASED component checks recent sensor readings against the
    documented normal operating ranges and maintenance schedule - this
    part is fully human-readable ("pressure out of range in 4/12 recent
    readings").
  - A lightweight ML component (per-sensor-type IsolationForest, from
    scikit-learn) adds a statistical anomaly signal that catches subtler
    multi-point drift the fixed thresholds might miss. Isolation Forest
    is used specifically because it still exposes a per-point anomaly
    score that can be reported as a contributing factor, rather than an
    opaque classification.
  - The two are blended with fixed, disclosed weights - no hidden logic.

Every score returned includes: the numeric risk, a risk_level bucket,
a list of plain-language contributing factors, a confidence value
(based on how much recent data was available), and a `based_on`
explanation string, satisfying the "never output a bare conclusion"
requirement.
"""
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.models.sensor import SensorReading
from backend.models.machine import Machine
from backend.data_generator.config import SENSOR_NORMAL_RANGES

RECENT_WINDOW = timedelta(hours=3)
SENSOR_TYPES = ["temperature", "pressure", "vibration", "humidity"]

# Blend weights for the final risk score - disclosed here rather than
# hidden inside the math, so the README/report can state them plainly.
WEIGHT_RULE_BASED = 0.45
WEIGHT_ML_ANOMALY = 0.35
WEIGHT_MAINTENANCE = 0.20

# Module-level cache of fitted IsolationForest models, keyed by sensor_type.
# Training on the full historical set is cheap for this dataset size and
# is redone once per process lifetime (call reset_model_cache() after
# ingesting a lot of new data if you want it retrained).
_MODEL_CACHE: dict[str, IsolationForest] = {}


def reset_model_cache():
    _MODEL_CACHE.clear()


def _get_or_train_model(db: Session, sensor_type: str) -> Optional[IsolationForest]:
    if sensor_type in _MODEL_CACHE:
        return _MODEL_CACHE[sensor_type]
    rows = (
        db.query(SensorReading.value)
        .filter(SensorReading.sensor_type == sensor_type, SensorReading.value.isnot(None))
        .all()
    )
    values = np.array([r[0] for r in rows]).reshape(-1, 1)
    if len(values) < 30:
        return None  # not enough history to train meaningfully
    model = IsolationForest(n_estimators=100, contamination=0.1, random_state=42)
    model.fit(values)
    _MODEL_CACHE[sensor_type] = model
    return model


def _ml_anomaly_score(db: Session, sensor_type: str, recent_values: list[float]) -> Optional[float]:
    """Returns an anomaly probability in [0,1] for the recent readings'
    mean, or None if there isn't enough history to score against."""
    model = _get_or_train_model(db, sensor_type)
    if model is None or not recent_values:
        return None
    mean_val = float(np.mean(recent_values))
    # decision_function: higher = more normal, lower/negative = more anomalous.
    raw = model.decision_function([[mean_val]])[0]
    # Squash to [0,1] anomaly probability; raw typically ranges roughly -0.5..0.5.
    anomaly_prob = float(np.clip(0.5 - raw, 0.0, 1.0))
    return anomaly_prob


def score_machine(db: Session, machine: Machine) -> dict:
    now = datetime.utcnow()
    window_start = now - RECENT_WINDOW
    factors: list[str] = []
    rule_components: list[float] = []
    ml_components: list[float] = []
    total_readings = 0

    for sensor_type in SENSOR_TYPES:
        readings = (
            db.query(SensorReading)
            .filter(
                SensorReading.machine_id == machine.machine_id,
                SensorReading.sensor_type == sensor_type,
                SensorReading.timestamp >= window_start,
            )
            .order_by(SensorReading.timestamp.asc())
            .all()
        )
        if not readings:
            continue
        total_readings += len(readings)
        lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
        present = [r for r in readings if r.value is not None]
        missing_count = len(readings) - len(present)
        out_of_range = [r for r in present if r.value < lo or r.value > hi]
        flagged = [r for r in present if r.data_quality_flag.value != "ok"]

        if out_of_range:
            ratio = len(out_of_range) / len(readings)
            rule_components.append(ratio)
            factors.append(
                f"{sensor_type} out of normal range ({lo}-{hi}) in {len(out_of_range)}/{len(readings)} "
                f"recent readings ({ratio:.0%})"
            )
        if missing_count:
            rule_components.append(0.3 * (missing_count / len(readings)))
            factors.append(f"{sensor_type} sensor dropped {missing_count}/{len(readings)} readings in the last 3h")
        if flagged and not out_of_range:
            ratio = len(flagged) / len(readings)
            rule_components.append(0.4 * ratio)
            factors.append(f"{sensor_type} data-quality flag raised on {len(flagged)}/{len(readings)} readings")

        if present:
            values = [r.value for r in present]
            ml_score = _ml_anomaly_score(db, sensor_type, values)
            if ml_score is not None and ml_score > 0.55:
                ml_components.append(ml_score)
                factors.append(f"{sensor_type} trending statistically unusual vs. historical baseline (anomaly score {ml_score:.2f})")
            elif ml_score is not None:
                ml_components.append(ml_score)

    # Maintenance-overdue component
    maintenance_component = 0.0
    if machine.next_maintenance_due:
        overdue_days = (now - machine.next_maintenance_due).total_seconds() / 86400
        if overdue_days > 0:
            maintenance_component = float(np.clip(overdue_days / 30.0, 0.0, 1.0))
            factors.append(f"machine is {overdue_days:.0f} day(s) overdue for scheduled maintenance")
        elif machine.last_maintenance_date:
            days_since = (now - machine.last_maintenance_date).total_seconds() / 86400
            if days_since > 25:
                maintenance_component = float(np.clip((days_since - 25) / 20.0, 0.0, 0.6))
                factors.append(f"{days_since:.0f} days since last maintenance service")

    rule_avg = float(np.mean(rule_components)) if rule_components else 0.0
    ml_avg = float(np.mean(ml_components)) if ml_components else 0.0

    combined = (
        WEIGHT_RULE_BASED * rule_avg
        + WEIGHT_ML_ANOMALY * ml_avg
        + WEIGHT_MAINTENANCE * maintenance_component
    )
    risk_score = round(float(np.clip(combined, 0.0, 1.0)) * 100, 1)

    if risk_score >= 66:
        risk_level = "high"
    elif risk_score >= 33:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Confidence scales with how much recent data backed the score.
    confidence = round(float(np.clip(total_readings / 24.0, 0.15, 0.98)), 2)

    if not factors:
        factors = ["all monitored sensors within normal range; no maintenance overdue"]

    based_on = (
        f"{total_readings} sensor readings across {len([s for s in SENSOR_TYPES])} sensor types "
        f"in the last {int(RECENT_WINDOW.total_seconds() // 3600)}h, plus maintenance schedule "
        f"(rule-based weight {WEIGHT_RULE_BASED}, ML anomaly weight {WEIGHT_ML_ANOMALY}, "
        f"maintenance weight {WEIGHT_MAINTENANCE})"
    )

    return {
        "machine_id": machine.machine_id,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "contributing_factors": factors,
        "confidence": confidence,
        "based_on": based_on,
        "computed_at": now,
    }
