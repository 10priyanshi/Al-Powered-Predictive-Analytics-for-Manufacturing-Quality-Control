from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from backend.models.database import Base


class AuditLog(Base):
    """
    Records every prediction, alert, recommendation, and human decision.
    Stores the acting user's role (not just user_id) so behavior can be
    reviewed by role, per the RBAC requirement.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, nullable=True)  # null for system/agent-generated entries
    username = Column(String(64), nullable=True)
    role = Column(String(32), nullable=False, index=True)  # "system" for agent actions
    action_type = Column(String(64), nullable=False, index=True)  # "approve", "reject", "predict", ...
    entity_type = Column(String(64), nullable=False, index=True)  # "recommendation", "defect", ...
    entity_id = Column(String(64), nullable=False, index=True)
    details_json = Column(Text, nullable=True)
    outcome = Column(String(32), nullable=True)  # "success", "failure", "approved", ...
