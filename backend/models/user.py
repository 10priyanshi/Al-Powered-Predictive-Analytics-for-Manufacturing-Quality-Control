from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import UserRole


class User(Base):
    """
    A demo user. `line_scope` restricts an Operator to a single line
    (e.g. "LINE3"); it is null/ignored for all other roles, whose scope
    is derived from their role itself (category-wide or plant-wide).
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(128), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    full_name = Column(String(128), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False, index=True)
    line_scope = Column(String(32), nullable=True)  # e.g. "LINE1" for operators
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<User {self.username} role={self.role}>"
