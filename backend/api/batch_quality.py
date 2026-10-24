import json
import random
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.batch_quality import BatchQualityScore
from backend.models.mes import WorkOrder, MaterialLot
from backend.models.machine import Machine
from backend.models.sensor import SensorReading
from backend.models.enums import WorkOrderStatus, DataQualityFlag, UserRole
from backend.data_generator.config import SENSOR_NORMAL_RANGES, SENSOR_UNITS
from backend.auth.dependencies import get_current_user, require_roles, CurrentUser
from backend.schemas.batch_quality import BatchQualityOut
from backend.services.rbac_scope import line_scope_for_user
from backend.agents import orchestrator

router = APIRouter(prefix="/api/batch-quality", tags=["batch_quality"])


def _to_out(row: BatchQualityScore, live_work_order_ids: set[str]) -> BatchQualityOut:
    return BatchQualityOut(
        work_order_id=row.work_order_id, batch_id=row.batch_id, machine_id=row.machine_id,
        quality_score=row.quality_score, risk_level=row.risk_level,
        probability_passing_qc=row.probability_passing_qc, confidence=row.confidence,
        contributing_factors=json.loads(row.contributing_factors_json),
        sampling_rate_pct=row.sampling_rate_pct, sampling_rationale=row.sampling_rationale or "",
        ai_summary=row.ai_summary, llm_generated=row.llm_generated,
        is_live=row.work_order_id in live_work_order_ids, computed_at=row.computed_at,
    )


def _live_work_order_ids(db: Session) -> set[str]:
    rows = db.query(WorkOrder.work_order_id).filter(WorkOrder.status == WorkOrderStatus.IN_PROGRESS).all()
    return {r[0] for r in rows}


@router.post("/demo-risk-mix", response_model=list[BatchQualityOut])
def seed_demo_risk_mix(
    current_user: CurrentUser = Depends(require_roles(UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    DEMO/PRESENTATION HELPER - not part of the core product logic.

    The batch quality score is computed live from real data, so in a
    freshly-seeded database every batch can legitimately land at "low
    risk" if nothing bad happened to be generated. That's correct
    behavior, but it makes for a flat demo screen. This endpoint
    deterministically injects realistic bad signals (out-of-range
    sensor readings, overdue maintenance, a degraded material lot) into
    a handful of the batches currently on screen, so a live audience
    sees the full low/medium/high spectrum instead of eight identical
    green gauges. It uses the exact same tables and columns every other
    agent reads - nothing about the scoring logic itself is touched or
    special-cased.
    """
    work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.in_([WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.COMPLETED]))
        .order_by(WorkOrder.planned_start.desc())
        .limit(8)
        .all()
    )
    if len(work_orders) < 6:
        raise HTTPException(400, "Not enough active batches to seed a demo risk mix - run a few simulate-ticks first.")

    high_risk_wos = work_orders[0:3]
    medium_risk_wos = work_orders[3:6]
    now = datetime.utcnow()
    rng = random.Random()

    def _inject_bad_sensors(machine_id: str, severity: str) -> None:
        for sensor_type, (lo, hi) in SENSOR_NORMAL_RANGES.items():
            mid, spread = (lo + hi) / 2, (hi - lo) / 2
            for i in range(6):  # spread across the recent scoring window
                ts = now - timedelta(minutes=15 * i)
                mult = rng.uniform(1.4, 2.0) if severity == "high" else rng.uniform(1.05, 1.3)
                value = round(mid + spread * mult * rng.choice([-1, 1]), 2)
                db.add(SensorReading(
                    machine_id=machine_id, sensor_type=sensor_type, value=value,
                    unit=SENSOR_UNITS[sensor_type], timestamp=ts, is_anomalous=True,
                    data_quality_flag=DataQualityFlag.OUT_OF_RANGE, scenario_tag="demo_risk_seed",
                ))

    touched_machines: set[str] = set()
    for wo in high_risk_wos:
        machine = db.query(Machine).filter(Machine.machine_id == wo.machine_id).first()
        if machine and machine.machine_id not in touched_machines:
            _inject_bad_sensors(machine.machine_id, "high")
            machine.next_maintenance_due = now - timedelta(days=4)  # overdue
            touched_machines.add(machine.machine_id)
        lot = db.query(MaterialLot).filter(MaterialLot.lot_id == wo.material_lot_id).first()
        if lot:
            lot.quality_score = min(lot.quality_score, 55.0)
            lot.prior_contamination_defects = max(lot.prior_contamination_defects, 2)

    for wo in medium_risk_wos:
        machine = db.query(Machine).filter(Machine.machine_id == wo.machine_id).first()
        if machine and machine.machine_id not in touched_machines:
            _inject_bad_sensors(machine.machine_id, "medium")
            machine.next_maintenance_due = now + timedelta(days=2)  # due soon, not yet overdue
            touched_machines.add(machine.machine_id)
        lot = db.query(MaterialLot).filter(MaterialLot.lot_id == wo.material_lot_id).first()
        if lot:
            lot.quality_score = min(lot.quality_score, 72.0)

    db.commit()

    rows = orchestrator.run_batch_quality_scan(db)
    live_ids = _live_work_order_ids(db)
    return [_to_out(r, live_ids) for r in rows]


@router.post("/scan", response_model=list[BatchQualityOut])
def scan_batches(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Scores every in-progress/recently-completed batch. This is the
    primary AI feature QA managers see first on the dashboard."""
    rows = orchestrator.run_batch_quality_scan(db)
    live_ids = _live_work_order_ids(db)
    return [_to_out(r, live_ids) for r in rows]


@router.get("", response_model=list[BatchQualityOut])
def list_batch_quality(
    auto_scan: bool = True,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the current batch quality scores, worst (lowest score,
    highest attention-need) first - scoped to the caller's line if
    they're an Operator, same as every other dashboard endpoint.

    If no scores exist yet at all (nobody has triggered a scan in this
    running instance), and auto_scan is true (the default), this runs
    one automatically rather than returning an empty list - this is
    what was causing scores to look "visible to one user, not another":
    whoever loaded the dashboard first, before anyone had scanned, saw
    nothing, while someone loading it after a scan saw everything, even
    though the underlying data and permissions were identical the whole
    time. Auto-scanning here means the first person to open the
    dashboard in a fresh session seeds it for everyone from then on.
    """
    existing_count = db.query(BatchQualityScore).count()
    if existing_count == 0 and auto_scan:
        orchestrator.run_batch_quality_scan(db)

    query = db.query(BatchQualityScore).order_by(BatchQualityScore.quality_score.asc())
    allowed_line = line_scope_for_user(current_user)
    if allowed_line:
        query = query.filter(BatchQualityScore.machine_id.like(f"{allowed_line}-%"))
    live_ids = _live_work_order_ids(db)
    return [_to_out(r, live_ids) for r in query.limit(50).all()]


@router.get("/{work_order_id}", response_model=BatchQualityOut)
def get_batch_quality(work_order_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(BatchQualityScore).filter(BatchQualityScore.work_order_id == work_order_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No quality score on file for {work_order_id}.")
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and not row.machine_id.startswith(f"{allowed_line}-"):
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")
    live_ids = _live_work_order_ids(db)
    return _to_out(row, live_ids)
