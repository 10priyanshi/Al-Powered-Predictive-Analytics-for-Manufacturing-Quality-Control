from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.learning_trend import LearningTrendPoint
from backend.models.recommendation import Recommendation
from backend.models.enums import UserRole
from backend.auth.dependencies import require_roles, CurrentUser
from backend.schemas.learning_trend import LearningTrendOut, LearningTrendPointOut

router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.get("/trend", response_model=LearningTrendOut)
def get_learning_trend(
    current_user: CurrentUser = Depends(require_roles(UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Recommendation-accuracy trend over time - the seeded historical
    weeks (illustrative baseline, per the data generator) plus one
    genuinely-computed "current" point built from real Approve/Reject/
    Modify outcomes recorded so far in this running instance. The two
    are never blended into a single number - each point discloses
    whether it's seed history or live-computed via is_historical_seed.
    """
    seeded = db.query(LearningTrendPoint).order_by(LearningTrendPoint.week_number.asc()).all()
    points = [
        LearningTrendPointOut(
            week_label=p.week_label, week_number=p.week_number, period_start=p.period_start,
            period_end=p.period_end, recommendation_accuracy_pct=p.recommendation_accuracy_pct,
            total_recommendations=p.total_recommendations, is_historical_seed=p.is_historical_seed,
        )
        for p in seeded
    ]

    # Live "current" point computed from real recommendation review outcomes.
    recs = db.query(Recommendation).all()
    decided = [r for r in recs if r.status.value in ("approved", "rejected", "modified")]
    if decided:
        good = len([r for r in decided if r.status.value in ("approved", "modified")])
        accuracy = round(good / len(decided) * 100, 1)
        next_week_num = (points[-1].week_number + 1) if points else 1
        now = datetime.utcnow()
        points.append(LearningTrendPointOut(
            week_label="Current", week_number=next_week_num,
            period_start=now - timedelta(days=7), period_end=now,
            recommendation_accuracy_pct=accuracy, total_recommendations=len(decided),
            is_historical_seed=False,
        ))
        note = (
            f"Weeks 1-{points[-2].week_number if len(points) > 1 else 0} are illustrative baseline history; "
            f"'Current' is computed live from {len(decided)} real Approve/Reject/Modify decision(s) made in "
            f"this running instance."
        )
    else:
        note = (
            "Weeks shown are illustrative baseline history (this instance has no reviewed recommendations yet - "
            "approve, reject, or modify a recommendation to see a live 'Current' point appear)."
        )

    return LearningTrendOut(points=points, note=note)
