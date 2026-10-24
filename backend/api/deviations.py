from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.machine import Machine
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.deviation import DeviationPredictionOut
from backend.agents import deviation_agent
from backend.services.rbac_scope import line_scope_for_user
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/deviations", tags=["deviations"])


class SimulateDeviationRequest(BaseModel):
    machine_id: str
    sensor_type: Literal["temperature", "pressure", "vibration", "humidity"] = "temperature"


@router.get("", response_model=list[DeviationPredictionOut])
def predict_deviations(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Predicts upcoming sensor threshold breaches across the plant (or the
    caller's line, if scoped), soonest-first. Unlike the Prediction
    Agent's live risk score, this looks forward: it only reports a
    sensor that ISN'T yet out of range but is trending toward crossing
    a boundary within the next two hours, with a disclosed probability
    based on how consistent that trend has been.
    """
    allowed_line = line_scope_for_user(current_user)
    predictions = deviation_agent.predict_deviations_plant_wide(db, allowed_line=allowed_line)
    return [DeviationPredictionOut(**p) for p in predictions]


@router.post("/simulate-imminent")
def simulate_imminent_deviation(
    payload: SimulateDeviationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Demo/test action: deterministically injects a short, steep sensor
    trend that the same real trend-fit math will predict crossing in
    ~8-10 minutes - i.e. a genuine "critical" severity result, on
    demand, rather than waiting for the organic synthetic data's
    deliberately-slow drift scenarios to happen to produce one.
    """
    machine = db.query(Machine).filter(Machine.machine_id == payload.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine {payload.machine_id} not found.")
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and not payload.machine_id.startswith(f"{allowed_line}-"):
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")

    result = deviation_agent.simulate_imminent_deviation(db, machine, payload.sensor_type)
    log_action(
        db, role=current_user.role.value, action_type="simulate_deviation", entity_type="deviation",
        entity_id=payload.machine_id, user_id=current_user.id, username=current_user.username,
        details=result, outcome="success",
    )
    return result
