from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base


class SimilarPastCase(Base):
    """
    A small in-app knowledge base of resolved incidents that the
    Recommendation Agent grounds new recommendations against instead
    of generating free-form text. Editable by the Quality Manager role.
    """
    __tablename__ = "similar_past_cases"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String(64), unique=True, index=True, nullable=False)
    defect_type = Column(String(64), nullable=False, index=True)
    category = Column(String(32), nullable=False, index=True)  # equipment/material/process/human
    description = Column(Text, nullable=False)
    root_cause = Column(Text, nullable=False)
    action_taken = Column(Text, nullable=False)
    outcome = Column(String(256), nullable=False)
    created_by = Column(String(64), nullable=False)
    editable = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
