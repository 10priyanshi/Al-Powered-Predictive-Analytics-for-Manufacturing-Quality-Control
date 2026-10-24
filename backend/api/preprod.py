import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.preprod_risk import PreProductionRiskScore
from backend.models.mes import WorkOrder
from backend.models.enums import WorkOrderStatus, UserRole
from backend.auth.dependencies import get_current_user, require_roles, CurrentUser
from backend.schemas.preprod import PreProdRiskOut, ValidationMetricsOut
from backend.agents import orchestrator, preprod_risk_agent

router = APIRouter(prefix="/api/preprod", tags=["preprod"])


def _to_out(row: PreProductionRiskScore) -> PreProdRiskOut:
    return PreProdRiskOut(
        work_order_id=row.work_order_id, risk_score=row.risk_score,
        contributing_reasons=json.loads(row.contributing_reasons_json), suggested_action=row.suggested_action,
        confidence=row.confidence, status=row.status.value, reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at, is_holdout=row.is_holdout, actual_outcome=row.actual_outcome,
        created_at=row.created_at,
    )


@router.post("/scan", response_model=list[PreProdRiskOut])
def scan_scheduled_batches(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Scores every currently-scheduled work order. Surfaced as the
    'Upcoming batches' risk queue on Production/Plant Head dashboards."""
    rows = orchestrator.run_preprod_scan(db)
    return [_to_out(r) for r in rows]


@router.get("/queue", response_model=list[PreProdRiskOut])
def get_queue(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns the current risk queue, ranked highest-risk first, for
    every work order that is still SCHEDULED (hasn't run yet)."""
    scheduled_ids = [
        wo.work_order_id for wo in db.query(WorkOrder).filter(WorkOrder.status == WorkOrderStatus.SCHEDULED).all()
    ]
    rows = (
        db.query(PreProductionRiskScore)
        .filter(PreProductionRiskScore.work_order_id.in_(scheduled_ids))
        .order_by(PreProductionRiskScore.risk_score.desc())
        .all()
    ) if scheduled_ids else []
    return [_to_out(r) for r in rows]


@router.get("/validation", response_model=ValidationMetricsOut)
def get_validation_metrics(
    threshold: float = 50.0,
    current_user: CurrentUser = Depends(require_roles(UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Honest precision/recall on a held-out set of already-completed work
    orders, re-scored as if they hadn't run yet and compared against
    what actually happened. Restricted to roles who'd act on model
    trustworthiness, per the spec's validation-discipline requirement.
    """
    result = preprod_risk_agent.evaluate_on_holdout(db, threshold=threshold)
    return ValidationMetricsOut(**result)
