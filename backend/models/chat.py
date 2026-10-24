from sqlalchemy import Column, Integer, String, DateTime, Text, Float, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base


class ChatMessage(Base):
    """
    One turn of the role-aware chatbot. `response` is always grounded
    in retrieved DB data (never freeform); `citations_json` records
    exactly what backed the answer, per the explainability requirement.
    """
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    role = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    response = Column(Text, nullable=False)
    citations_json = Column(Text, nullable=True)  # what data backed the answer
    confidence = Column(Float, nullable=True)
    llm_generated = Column(Boolean, nullable=False, default=False)  # True if Claude phrased the response (vs. rule-based fallback)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
