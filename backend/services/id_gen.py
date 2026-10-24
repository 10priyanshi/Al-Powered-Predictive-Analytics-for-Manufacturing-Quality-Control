"""
Generates sequential, human-readable IDs (DEF-100123, ALT-000045, ...)
by counting existing rows. Fine for a single-process local demo; a
production system would use a DB sequence or UUID instead to avoid a
race condition under concurrent writers.
"""
from sqlalchemy.orm import Session
from sqlalchemy import func


def next_id(db: Session, model, id_column, prefix: str, start: int, pad: int = 6) -> str:
    count = db.query(func.count()).select_from(model).scalar() or 0
    return f"{prefix}-{start + count:0{pad}d}"
