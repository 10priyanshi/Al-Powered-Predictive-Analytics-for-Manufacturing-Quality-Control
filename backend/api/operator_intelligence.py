from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.enums import UserRole
from backend.auth.dependencies import require_roles, CurrentUser
from backend.schemas.operator_intelligence import OperatorIntelligenceOut
from backend.services import operator_signals

router = APIRouter(prefix="/api/operator-intelligence", tags=["operator_intelligence"])


@router.get("", response_model=OperatorIntelligenceOut)
def get_operator_intelligence(
    current_user: CurrentUser = Depends(require_roles(
        UserRole.PRODUCTION, UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN
    )),
    db: Session = Depends(get_db),
):
    """
    Shift/operator performance analysis - restricted to roles who'd
    actually act on it (Production supervisors, Quality Manager, Plant
    Head, Admin). Everything here is computed live from real inspection
    outcomes tied to production logs, not a separate tracked dataset.
    """
    operators = operator_signals.all_operator_stats(db)
    shifts = operator_signals.shift_stats(db)
    insights = operator_signals.generate_insights(operators, shifts)
    return OperatorIntelligenceOut(operators=operators, shifts=shifts, insights=insights)
