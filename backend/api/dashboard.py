from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.machine import Machine
from backend.models.alert import Alert
from backend.models.recommendation import Recommendation
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.dashboard import MachineStatusOut, ModelPerformanceOut
from backend.agents import prediction_agent
from backend.services.rbac_scope import line_scope_for_user
from backend.services.machine_status import get_machine_operational_status, get_running_work_orders_by_machine

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/status", response_model=list[MachineStatusOut])
def machine_status_grid(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    The live line/machine status grid. Same endpoint for every role -
    scope is applied server-side (Operator: own line only; everyone
    else: plant-wide), the frontend just renders whatever comes back.
    """
    query = db.query(Machine)
    allowed_line = line_scope_for_user(current_user)
    if allowed_line:
        query = query.filter(Machine.machine_id.like(f"{allowed_line}-%"))
    machines = query.all()
    running_work_orders = get_running_work_orders_by_machine(db)

    out = []
    for m in machines:
        result = prediction_agent.score_machine(db, m)
        active_alerts = (
            db.query(Alert)
            .filter(Alert.machine_id == m.machine_id, Alert.status != "acknowledged")
            .count()
        )
        op_status = get_machine_operational_status(db, m, current_work_orders=running_work_orders)
        active_wo = running_work_orders.get(m.machine_id)
        out.append(MachineStatusOut(
            machine_id=m.machine_id, machine_name=m.name, line_id=m.machine_id.split("-")[0],
            machine_type=m.machine_type, risk_score=result["risk_score"], risk_level=result["risk_level"],
            operational_status=op_status, active_work_order_id=active_wo.work_order_id if active_wo else None,
            active_alert_count=active_alerts, last_maintenance_date=m.last_maintenance_date,
        ))
    return out


@router.get("/model-performance", response_model=ModelPerformanceOut)
def model_performance(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Feedback-loop metric: what fraction of agent recommendations humans
    have approved/rejected/modified, so the dashboard can show model
    performance over time rather than just asserting accuracy.
    """
    recs = db.query(Recommendation).all()
    approved = len([r for r in recs if r.status.value == "approved"])
    rejected = len([r for r in recs if r.status.value == "rejected"])
    modified = len([r for r in recs if r.status.value == "modified"])
    pending = len([r for r in recs if r.status.value == "pending"])
    decided = approved + rejected + modified
    approval_rate = round((approved + modified) / decided, 3) if decided else None
    return ModelPerformanceOut(
        total_recommendations=len(recs), approved=approved, rejected=rejected,
        modified=modified, pending=pending, approval_rate=approval_rate,
    )
