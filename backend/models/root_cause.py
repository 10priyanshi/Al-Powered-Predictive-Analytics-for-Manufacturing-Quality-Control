from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base


class RootCauseAnalysis(Base):
    """
    Output of the RCA Agent for one defect. `category_split` stores a
    confidence-weighted split across Equipment/Material/Process/Human
    as JSON text, e.g. '{"equipment": 0.6, "process": 0.4}', so a
    defect can be attributed to more than one bucket rather than
    forced into a single category.
    """
    __tablename__ = "root_cause_analyses"

    id = Column(Integer, primary_key=True, index=True)
    defect_id = Column(String(64), ForeignKey("defects.defect_id"), index=True, nullable=False)
    category_split_json = Column(Text, nullable=False)  # {"equipment": 0.6, "process": 0.4}
    primary_category = Column(String(32), nullable=False, index=True)  # highest-weight category
    contributing_factors_json = Column(Text, nullable=False)  # list of factor strings
    explanation = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)  # 0-1
    routed_role = Column(String(32), nullable=False, index=True)  # role this routes to
    llm_generated = Column(Boolean, nullable=False, default=False)  # True if the explanation prose was written by Claude
    created_at = Column(DateTime(timezone=True), server_default=func.now())
