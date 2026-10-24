"""
Defect-injection logic for the synthetic sensor data generator.

Produces realistic sensor time series with five documented scenario
types layered onto a normal baseline:

  1. gradual_drift          - a slow, sustained shift away from baseline
                               (e.g. a bearing wearing down over days)
  2. sudden_spike            - a short, sharp excursion then recovery
  3. correlated_multi_sensor - two sensors on the same machine degrade
                               together (e.g. vibration + temperature)
  4. missing_data            - a gap in the series (sensor dropout)
  5. false_positive          - an anomalous-looking reading that does
                               NOT lead to a defect (tests that agents
                               don't over-alert on noise alone)

Each generated reading carries a `scenario_tag` for generator-side
debugging/traceability; downstream agents must not rely on this tag
(it would be cheating) — they only see value/timestamp/flags.
"""
import random
from datetime import timedelta
from backend.data_generator.config import SENSOR_NORMAL_RANGES, SENSOR_UNITS


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def generate_baseline_series(machine_id: str, sensor_type: str, start, end, interval_minutes: int, rng: random.Random):
    """Generate a normal-noise baseline series with no injected scenario."""
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    mid = (lo + hi) / 2
    spread = (hi - lo) / 2
    readings = []
    t = start
    while t <= end:
        value = _clamp(rng.gauss(mid, spread * 0.18), lo - spread * 0.05, hi + spread * 0.05)
        readings.append({
            "machine_id": machine_id, "sensor_type": sensor_type, "value": round(value, 2),
            "unit": SENSOR_UNITS[sensor_type], "timestamp": t, "is_anomalous": False,
            "data_quality_flag": "ok", "scenario_tag": None,
        })
        t += timedelta(minutes=interval_minutes)
    return readings


def inject_gradual_drift(readings: list, rng: random.Random, sensor_type: str):
    """
    Ramps the back portion of the series steadily upward past the
    normal range, simulating equipment wearing down over time.
    """
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    n = len(readings)
    drift_start = int(n * rng.uniform(0.55, 0.7))
    drift_span = n - drift_start
    max_drift = (hi - lo) * rng.uniform(0.5, 0.9)
    for i in range(drift_start, n):
        progress = (i - drift_start) / max(1, drift_span)
        readings[i]["value"] = round(readings[i]["value"] + max_drift * progress, 2)
        if readings[i]["value"] > hi:
            readings[i]["is_anomalous"] = True
            readings[i]["data_quality_flag"] = "out_of_range"
        readings[i]["scenario_tag"] = "gradual_drift"
    return readings


def inject_sudden_spike(readings: list, rng: random.Random, sensor_type: str):
    """A short, sharp excursion (3-6 consecutive readings) then recovery."""
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    n = len(readings)
    if n < 10:
        return readings
    spike_start = rng.randint(int(n * 0.2), int(n * 0.8))
    spike_len = rng.randint(3, 6)
    spike_magnitude = (hi - lo) * rng.uniform(1.2, 2.0)
    for i in range(spike_start, min(n, spike_start + spike_len)):
        readings[i]["value"] = round(readings[i]["value"] + spike_magnitude, 2)
        readings[i]["is_anomalous"] = True
        readings[i]["data_quality_flag"] = "out_of_range"
        readings[i]["scenario_tag"] = "sudden_spike"
    return readings


def inject_missing_data(readings: list, rng: random.Random):
    """Blanks out a short contiguous run of readings (sensor dropout)."""
    n = len(readings)
    if n < 10:
        return readings
    gap_start = rng.randint(int(n * 0.1), int(n * 0.85))
    gap_len = rng.randint(2, 5)
    for i in range(gap_start, min(n, gap_start + gap_len)):
        readings[i]["value"] = None
        readings[i]["data_quality_flag"] = "missing"
        readings[i]["scenario_tag"] = "missing_data"
    return readings


def inject_uncalibrated_run(readings: list, rng: random.Random, sensor_type: str):
    """A stretch of readings offset by a constant bias — sensor needs calibration."""
    n = len(readings)
    if n < 10:
        return readings
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    bias = (hi - lo) * rng.choice([-1, 1]) * rng.uniform(0.15, 0.3)
    run_start = rng.randint(0, int(n * 0.6))
    run_len = rng.randint(6, 12)
    for i in range(run_start, min(n, run_start + run_len)):
        if readings[i]["value"] is not None:
            readings[i]["value"] = round(readings[i]["value"] + bias, 2)
        readings[i]["data_quality_flag"] = "uncalibrated"
        readings[i]["scenario_tag"] = "uncalibrated_run"
    return readings


def inject_false_positive(readings: list, rng: random.Random, sensor_type: str):
    """
    A brief anomalous-looking blip that recovers immediately and is NOT
    correlated with any other signal — used to test that agents don't
    over-alert on noise alone.
    """
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    n = len(readings)
    if n < 5:
        return readings
    idx = rng.randint(0, n - 1)
    readings[idx]["value"] = round(readings[idx]["value"] + (hi - lo) * rng.uniform(0.6, 0.9), 2)
    readings[idx]["is_anomalous"] = True
    readings[idx]["data_quality_flag"] = "out_of_range"
    readings[idx]["scenario_tag"] = "false_positive"
    return readings


SCENARIO_INJECTORS = {
    "gradual_drift": inject_gradual_drift,
    "sudden_spike": inject_sudden_spike,
    "missing_data": inject_missing_data,
    "uncalibrated_run": inject_uncalibrated_run,
    "false_positive": inject_false_positive,
}
