import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.recommendation import Recommendation
from backend.models.preprod_risk import PreProductionRiskScore
from backend.models.enums import RecommendationStatus
from backend.auth.dependencies import get_current_user, CurrentUser, can_review_recommendation
from backend.schemas.recommendation import RecommendationOut, ReviewRecommendationRequest
from backend.services.rbac_scope import assigned_role_scope_for_recommendations
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


def _to_out(r: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        recommendation_id=r.recommendation_id, source_type=r.source_type, source_id=r.source_id,
        action_text=r.action_text, reasoning=r.reasoning, confidence=r.confidence,
        similar_case_ids=json.loads(r.similar_case_ids_json or "[]"), assigned_role=r.assigned_role,
        status=r.status.value, reviewed_by=r.reviewed_by, reviewed_at=r.reviewed_at,
        modification_note=r.modification_note, llm_generated=r.llm_generated, created_at=r.created_at,
    )


@router.get("", response_model=list[RecommendationOut])
def list_recommendations(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Recommendation).order_by(Recommendation.created_at.desc())
    scope_role = assigned_role_scope_for_recommendations(current_user)
    if scope_role:
        query = query.filter(Recommendation.assigned_role == scope_role)
    if status_filter:
        query = query.filter(Recommendation.status == RecommendationStatus(status_filter))
    return [_to_out(r) for r in query.limit(200).all()]


@router.get("/{recommendation_id}", response_model=RecommendationOut)
def get_recommendation(recommendation_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = db.query(Recommendation).filter(Recommendation.recommendation_id == recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail=f"Recommendation {recommendation_id} not found.")
    return _to_out(rec)


@router.post("/{recommendation_id}/review", response_model=RecommendationOut)
def review_recommendation(
    recommendation_id: str,
    payload: ReviewRecommendationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    The human oversight step required for every agent-generated
    recommendation. Only the role the recommendation was routed to (or
    Quality Manager / Plant Head, who can act on anything) may review it.
    The action, reviewer, and timestamp are always logged to the audit
    trail - this is the record that proves no action was fully autonomous.
    """
    rec = db.query(Recommendation).filter(Recommendation.recommendation_id == recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail=f"Recommendation {recommendation_id} not found.")
    if not can_review_recommendation(current_user, rec.assigned_role):
        raise HTTPException(
            status_code=403,
            detail=f"Recommendation is routed to '{rec.assigned_role}'; your role cannot review it.",
        )
    if rec.status != RecommendationStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Recommendation is already '{rec.status.value}', cannot re-review.")
    if payload.action == "modify" and not payload.modification_note:
        raise HTTPException(status_code=400, detail="modification_note is required when action is 'modify'.")

    status_map = {
        "approve": RecommendationStatus.APPROVED,
        "reject": RecommendationStatus.REJECTED,
        "modify": RecommendationStatus.MODIFIED,
    }
    rec.status = status_map[payload.action]
    rec.reviewed_by = current_user.username
    rec.reviewed_at = datetime.utcnow()
    rec.modification_note = payload.modification_note
    db.commit()
    db.refresh(rec)

    # If this recommendation backs a pre-production risk score, mirror the decision there too.
    preprod = db.query(PreProductionRiskScore).filter(PreProductionRiskScore.work_order_id == rec.source_id).first()
    if rec.source_type == "preprod_batch" and preprod:
        preprod.status = rec.status
        preprod.reviewed_by = current_user.username
        preprod.reviewed_at = rec.reviewed_at
        db.commit()

    log_action(
        db, role=current_user.role.value, action_type=f"recommendation_{payload.action}",
        entity_type="recommendation", entity_id=rec.recommendation_id, user_id=current_user.id,
        username=current_user.username, details={"modification_note": payload.modification_note},
        outcome=rec.status.value,
    )
    return _to_out(rec)
