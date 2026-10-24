"""
QualityGuard AI - FastAPI application entrypoint.

Run locally with:  uvicorn backend.main:app --reload --app-dir .
(from the project root, so the `backend` package resolves)

Serves the API under /api/* and the static frontend (built in Phase 3)
from the project-root `frontend/` directory at `/`.
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.models.database import init_db, SessionLocal
from backend.services.data_freshness import catch_up_sensor_data
from backend.api import (
    auth, ingest, predictions, dashboard, defects, recommendations,
    alerts, reports, audit, chat, preprod, events, knowledge_base, users,
    batch_quality, deviations, operator_intelligence, learning_trend,
)

app = FastAPI(
    title="QualityGuard AI",
    description="Predictive quality control platform for manufacturing - API",
    version="1.0.0",
)

# CORS is configured explicitly rather than left wide open, per the
# security-basics requirement. Local dev origins are allowed since the
# frontend is a static site that may be served from a different port
# than uvicorn during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5500", "http://127.0.0.1:5500",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "null",  # allows opening frontend/*.html directly as a file:// page during local dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    # Top up sensor data to "now" if the database is stale relative to
    # wall-clock time (e.g. the server started well after the data was
    # generated) - see backend/services/data_freshness.py for why this
    # matters for the live-window agents (Prediction, Deviation Prediction).
    db = SessionLocal()
    try:
        inserted = catch_up_sensor_data(db)
        if inserted:
            print(f"[startup] Data was stale - backfilled {inserted} sensor readings to bring the live window up to date.")
    finally:
        db.close()


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "QualityGuard AI"}


# --- Routers -----------------------------------------------------------
app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(predictions.router)
app.include_router(dashboard.router)
app.include_router(defects.router)
app.include_router(recommendations.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(audit.router)
app.include_router(chat.router)
app.include_router(preprod.router)
app.include_router(events.router)
app.include_router(knowledge_base.router)
app.include_router(users.router)
app.include_router(batch_quality.router)
app.include_router(deviations.router)
app.include_router(operator_intelligence.router)
app.include_router(learning_trend.router)


# --- Static frontend -----------------------------------------------------
# Mounted last so it never shadows an /api/* route. The frontend directory
# lives at the project root (sibling of backend/), built out in Phase 3.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIR = os.path.join(_BASE_DIR, "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
