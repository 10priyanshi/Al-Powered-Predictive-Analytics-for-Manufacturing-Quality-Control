from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base


class LearningTrendPoint(Base):
    """
    One week's recommendation-accuracy data point for the Continuous
    Learning dashboard. `is_historical_seed` marks the illustrative
    baseline weeks the data generator seeds (per the brief's instruction
    to use realistic sample data where real history isn't available yet)
    versus a week computed later from genuine Approve/Reject/Modify
    review outcomes once the app has actually been used - the API
    appends that live current-week figure on top of the seeded history
    rather than mixing the two silently.
    """
    __tablename__ = "learning_trend_points"

    id = Column(Integer, primary_key=True, index=True)
    week_label = Column(String(16), nullable=False)  # "Week 1".."Week 6"
    week_number = Column(Integer, nullable=False, index=True)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    recommendation_accuracy_pct = Column(Float, nullable=False)  # (approved+modified) / decided, as a percent
    total_recommendations = Column(Integer, nullable=False)
    is_historical_seed = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
