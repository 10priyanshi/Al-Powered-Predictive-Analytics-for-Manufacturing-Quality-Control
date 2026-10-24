"""Seeds one demo user per role, per the config.DEMO_USERS list, with
properly bcrypt-hashed passwords (never plaintext, even for demo data)."""
from sqlalchemy.orm import Session
from backend.models.user import User
from backend.models.enums import UserRole
from backend.auth.security import hash_password
from backend.data_generator.config import DEMO_USERS


def seed_users(db: Session):
    created = []
    for u in DEMO_USERS:
        existing = db.query(User).filter(User.username == u["username"]).first()
        if existing:
            continue
        user = User(
            username=u["username"],
            email=u["email"],
            password_hash=hash_password(u["password"]),
            full_name=u["full_name"],
            role=UserRole(u["role"]),
            line_scope=u["line_scope"],
            is_active=True,
        )
        db.add(user)
        created.append(u["username"])
    db.commit()
    return created
