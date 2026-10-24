from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.machine import Machine
from backend.models.sensor import SensorReading
from backend.models.maintenance import MaintenanceLog
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.prediction import RiskScoreOut
from backend.agents import prediction_agent
from backend.services.rbac_scope import line_scope_for_user

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


def _check_machine_scope(machine: Machine, current_user: CurrentUser):
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and machine.machine_id.split("-")[0] != allowed_line:
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")


@router.get("/machine/{machine_id}/readings")
def get_machine_readings(
    machine_id: str, hours: int = 6,
    current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Recent raw sensor readings for the drill-down trend charts, plus
    the machine's maintenance history - everything the frontend needs
    to render 'recent readings, active risk factors, and history'."""
    machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine {machine_id} not found.")
    _check_machine_scope(machine, current_user)

    since = datetime.utcnow() - timedelta(hours=hours)
    readings = (
        db.query(SensorReading)
        .filter(SensorReading.machine_id == machine_id, SensorReading.timestamp >= since)
        .order_by(SensorReading.timestamp.asc())
        .all()
    )
    maintenance = (
        db.query(MaintenanceLog)
        .filter(MaintenanceLog.machine_id == machine_id)
        .order_by(MaintenanceLog.timestamp.desc())
        .limit(10)
        .all()
    )
    by_sensor: dict[str, list[dict]] = {"temperature": [], "pressure": [], "vibration": [], "humidity": []}
    for r in readings:
        by_sensor[r.sensor_type.value].append({
            "timestamp": r.timestamp.isoformat(), "value": r.value,
            "is_anomalous": r.is_anomalous, "data_quality_flag": r.data_quality_flag.value,
        })
    return {
        "machine_id": machine.machine_id, "name": machine.name, "machine_type": machine.machine_type,
        "units_per_minute": machine.units_per_minute,
        "last_maintenance_date": machine.last_maintenance_date,
        "next_maintenance_due": machine.next_maintenance_due,
        "readings": by_sensor,
        "maintenance_history": [
            {"maintenance_type": m.maintenance_type.value, "performed_by": m.performed_by,
             "timestamp": m.timestamp.isoformat(), "notes": m.notes}
            for m in maintenance
        ],
    }


@router.get("/machine/{machine_id}", response_model=RiskScoreOut)
def get_machine_risk(machine_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine {machine_id} not found.")
    _check_machine_scope(machine, current_user)
    result = prediction_agent.score_machine(db, machine)
    return RiskScoreOut(line_id=machine.machine_id.split("-")[0], **result)


@router.get("/line/{line_code}", response_model=list[RiskScoreOut])
def get_line_risk(line_code: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and line_code != allowed_line:
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")
    machines = db.query(Machine).filter(Machine.machine_id.like(f"{line_code}-%")).all()
    if not machines:
        raise HTTPException(status_code=404, detail=f"No machines found for line {line_code}.")
    results = []
    for m in machines:
        result = prediction_agent.score_machine(db, m)
        results.append(RiskScoreOut(line_id=line_code, **result))
    return results
