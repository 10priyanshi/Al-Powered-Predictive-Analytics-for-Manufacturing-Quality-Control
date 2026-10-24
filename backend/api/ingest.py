import random
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.sensor import SensorReading
from backend.models.inspection import Inspection
from backend.models.machine import Machine
from backend.models.enums import DataQualityFlag, InspectionResult, DefectType
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.ingest import IngestBatchRequest, IngestBatchResponse
from backend.data_generator.config import SENSOR_NORMAL_RANGES, SENSOR_UNITS
from backend.agents import orchestrator
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _quality_flag_for_value(sensor_type: str, value: float | None) -> DataQualityFlag:
    if value is None:
        return DataQualityFlag.MISSING
    lo, hi = SENSOR_NORMAL_RANGES[sensor_type]
    if value < lo or value > hi:
        return DataQualityFlag.OUT_OF_RANGE
    return DataQualityFlag.OK


@router.post("/batch", response_model=IngestBatchResponse)
def ingest_batch(
    payload: IngestBatchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Accepts a batch of sensor readings and/or inspections "arriving in
    real time" from the shop floor. Data-quality flags are applied
    before anything reaches the Prediction Agent, per the data-quality
    layer requirement. Failed inspections immediately trigger the
    Defect -> RCA -> Recommendation pipeline; sensor readings feed the
    next prediction cycle.
    """
    sensor_count, inspection_count = 0, 0
    defects_created, alerts_created, recs_created = 0, 0, 0
    touched_machine_ids: set[str] = set()

    for r in payload.sensor_readings:
        machine = db.query(Machine).filter(Machine.machine_id == r.machine_id).first()
        if machine is None:
            continue  # unknown machine_id - skip rather than silently invent one
        flag = _quality_flag_for_value(r.sensor_type, r.value)
        is_anomalous = flag == DataQualityFlag.OUT_OF_RANGE
        db.add(SensorReading(
            machine_id=r.machine_id, sensor_type=r.sensor_type, value=r.value,
            unit=SENSOR_UNITS[r.sensor_type], timestamp=r.timestamp or datetime.utcnow(),
            is_anomalous=is_anomalous, data_quality_flag=flag, scenario_tag="live_ingest",
        ))
        sensor_count += 1
        touched_machine_ids.add(r.machine_id)
    db.commit()

    for i in payload.inspections:
        machine = db.query(Machine).filter(Machine.machine_id == i.machine_id).first()
        if machine is None:
            continue
        insp = Inspection(
            batch_id=i.batch_id, machine_id=i.machine_id, station=i.station, inspector=i.inspector,
            result=InspectionResult(i.result), defect_type=DefectType(i.defect_type) if i.defect_type else None,
            defect_location=i.defect_location, timestamp=datetime.utcnow(), notes=i.notes,
        )
        db.add(insp)
        db.commit()
        db.refresh(insp)
        inspection_count += 1
        result = orchestrator.process_inspection(db, insp)
        if result["defect"]:
            defects_created += 1
        if result["recommendation"]:
            recs_created += 1

    # Re-score any machine that just received new sensor data - this is
    # what makes freshly ingested data actually surface as alerts.
    for machine_id in touched_machine_ids:
        machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
        if machine:
            cycle_result = orchestrator.run_prediction_for_machine(db, machine)
            if cycle_result["created"]["alert"]:
                alerts_created += 1
            if cycle_result["created"]["defect"]:
                defects_created += 1
            if cycle_result["created"]["recommendation"]:
                recs_created += 1

    log_action(
        db, role=current_user.role.value, action_type="ingest_batch", entity_type="ingest",
        entity_id=f"{sensor_count}sensors_{inspection_count}inspections", user_id=current_user.id,
        username=current_user.username,
        details={"sensor_readings": sensor_count, "inspections": inspection_count}, outcome="success",
    )

    return IngestBatchResponse(
        sensor_readings_ingested=sensor_count, inspections_ingested=inspection_count,
        defects_created=defects_created, alerts_created=alerts_created, recommendations_created=recs_created,
    )


@router.post("/simulate-tick", response_model=IngestBatchResponse)
def simulate_tick(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Generates one new realistic reading per machine per sensor type at
    the current timestamp (a lightweight stand-in for a live sensor
    feed/scheduled simulation loop), then runs a full prediction cycle.
    Lets the dashboard demo "real-time" behavior without a background
    process - call this from the frontend's poll loop or a manual button.
    """
    rng = random.Random()
    machines = db.query(Machine).all()
    now = datetime.utcnow()
    sensor_count = 0
    for machine in machines:
        for sensor_type, (lo, hi) in SENSOR_NORMAL_RANGES.items():
            mid, spread = (lo + hi) / 2, (hi - lo) / 2
            # Small chance of a fresh anomalous blip so the live demo has movement to show.
            if rng.random() < 0.08:
                value = mid + spread * rng.uniform(1.1, 1.6) * rng.choice([-1, 1])
            else:
                value = rng.gauss(mid, spread * 0.18)
            flag = _quality_flag_for_value(sensor_type, value)
            db.add(SensorReading(
                machine_id=machine.machine_id, sensor_type=sensor_type, value=round(value, 2),
                unit=SENSOR_UNITS[sensor_type], timestamp=now, is_anomalous=flag == DataQualityFlag.OUT_OF_RANGE,
                data_quality_flag=flag, scenario_tag="simulated_tick",
            ))
            sensor_count += 1
    db.commit()

    alerts_created, defects_created, recs_created = 0, 0, 0
    for machine in machines:
        cycle_result = orchestrator.run_prediction_for_machine(db, machine)
        if cycle_result["created"]["alert"]:
            alerts_created += 1
        if cycle_result["created"]["defect"]:
            defects_created += 1
        if cycle_result["created"]["recommendation"]:
            recs_created += 1

    return IngestBatchResponse(
        sensor_readings_ingested=sensor_count, inspections_ingested=0,
        defects_created=defects_created, alerts_created=alerts_created, recommendations_created=recs_created,
    )
