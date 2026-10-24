"""
Orchestrator
=============
Coordinates the individual agents into the end-to-end pipelines
described in the integration flow: data ingestion -> Prediction Agent
-> (threshold crossed) -> Alert -> Defect -> RCA Agent -> Recommendation
Agent -> human Approve/Reject/Modify. Also drives the Pre-Production
Risk Agent and Event-to-Unit Traceability Agent pipelines.

No agent here ever takes an autonomous action on the factory floor -
every recommendation this orchestrator creates lands in PENDING status
and requires a human review step through the API before anything is
considered "decided".
"""
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from backend.models.machine import Machine
from backend.models.sensor import SensorReading
from backend.models.inspection import Inspection
from backend.models.defect import Defect
from backend.models.root_cause import RootCauseAnalysis
from backend.models.recommendation import Recommendation
from backend.models.alert import Alert
from backend.models.mes import WorkOrder
from backend.models.preprod_risk import PreProductionRiskScore
from backend.models.event_trace import EventToUnitTrace
from backend.models.enums import (
    DefectSource, DefectStatus, AlertSeverity, RecommendationStatus,
    InspectionResult, WorkOrderStatus, EventType, EventTraceStatus,
)
from backend.services.id_gen import next_id
from backend.services.audit_service import log_action

from backend.agents import prediction_agent, rca_agent, recommendation_agent
from backend.agents import preprod_risk_agent, event_trace_agent
from backend.agents import batch_quality_agent
from backend.models.batch_quality import BatchQualityScore
from backend.services import llm_service

# Prediction risk-score thresholds that trigger action.
ALERT_THRESHOLD = 55.0     # >= this: raise an alert
DEFECT_THRESHOLD = 75.0    # >= this: also open a predicted defect + run RCA/RAC
ALERT_COOLDOWN = timedelta(hours=1)  # avoid re-alerting the same machine every poll


def _severity_for_score(score: float) -> AlertSeverity:
    if score >= 80:
        return AlertSeverity.CRITICAL
    if score >= 55:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


def run_rca_and_recommendation(db: Session, defect: Defect) -> tuple[RootCauseAnalysis, Recommendation]:
    rca_result = rca_agent.analyze_defect(db, defect)
    rca = RootCauseAnalysis(
        defect_id=defect.defect_id,
        category_split_json=json.dumps(rca_result["category_split"]),
        primary_category=rca_result["primary_category"],
        contributing_factors_json=json.dumps(rca_result["contributing_factors"]),
        explanation=rca_result["explanation"],
        confidence=rca_result["confidence"],
        routed_role=rca_result["routed_role"],
        llm_generated=rca_result.get("llm_generated", False),
    )
    db.add(rca)
    db.flush()
    log_action(db, role="system", action_type="rca_analyze", entity_type="defect",
               entity_id=defect.defect_id, details=rca_result, outcome="success", commit=False)

    rec_result = recommendation_agent.generate_recommendation(db, defect, rca_result)
    rec_id = next_id(db, Recommendation, "recommendation_id", "REC", 100000)
    rec = Recommendation(
        recommendation_id=rec_id, source_type="defect", source_id=defect.defect_id,
        action_text=rec_result["action_text"], reasoning=rec_result["reasoning"],
        confidence=rec_result["confidence"],
        similar_case_ids_json=json.dumps(rec_result["similar_case_ids"]),
        assigned_role=rec_result["assigned_role"], status=RecommendationStatus.PENDING,
        llm_generated=rec_result.get("llm_generated", False),
    )
    db.add(rec)
    db.flush()
    log_action(db, role="system", action_type="recommend", entity_type="recommendation",
               entity_id=rec.recommendation_id, details=rec_result, outcome="success", commit=False)

    # Defects that arrive via a predicted risk crossing already got their
    # alert BEFORE the defect was even created (see run_prediction_for_machine
    # below) - raising a second one here would be a duplicate. Defects that
    # arrive via a manual operator flag or a failed inspection have never
    # had any alert raised for them at all until now, which meant "a real
    # defect was just encountered" produced no live notification anywhere -
    # fixed here so every defect, regardless of source, reliably surfaces
    # on the Active Alerts panel the moment it's recorded.
    if defect.source != DefectSource.PREDICTION:
        alert_id = next_id(db, Alert, "alert_id", "ALT", 100000)
        severity = AlertSeverity.CRITICAL if defect.source == DefectSource.MANUAL else AlertSeverity.WARNING
        source_label = {"manual": "Operator-flagged", "inspection": "Inspection-flagged"}.get(defect.source.value, "Reported")
        alert = Alert(
            alert_id=alert_id, machine_id=defect.machine_id, line_id=defect.machine_id.split("-")[0],
            severity=severity,
            title=f"{source_label} defect on {defect.machine_id}: {defect.defect_type.value.replace('_', ' ')}",
            description=(
                f"{rca_result['primary_category'].capitalize()} root cause identified "
                f"({rca_result['confidence']:.0%} confidence), routed to {rca_result['routed_role']}. "
                f"Recommendation {rec.recommendation_id} is pending review."
            ),
            trigger_data_json=json.dumps({"defect_id": defect.defect_id, "rca": rca_result}, default=str),
            source_agent="defect_pipeline", related_defect_id=defect.defect_id,
        )
        db.add(alert)
        log_action(db, role="system", action_type="defect_alert", entity_type="alert",
                   entity_id=alert_id, details={"defect_id": defect.defect_id}, outcome="success", commit=False)

    db.commit()
    return rca, rec


def _recent_alert_exists(db: Session, machine_id: str, now: datetime) -> bool:
    cutoff = now - ALERT_COOLDOWN
    return (
        db.query(Alert)
        .filter(Alert.machine_id == machine_id, Alert.created_at >= cutoff)
        .count()
        > 0
    )


def run_prediction_for_machine(db: Session, machine: Machine, force_alert: bool = False) -> dict:
    """Scores one machine and, if warranted, raises an alert and/or opens
    a predicted defect + runs RCA/Recommendation. Returns the score plus
    whatever was created, for the API layer to report back."""
    result = prediction_agent.score_machine(db, machine)
    now = result["computed_at"]
    created = {"alert": None, "defect": None, "recommendation": None}

    if result["risk_score"] >= ALERT_THRESHOLD and (force_alert or not _recent_alert_exists(db, machine.machine_id, now)):
        alert_id = next_id(db, Alert, "alert_id", "ALT", 100000)
        alert = Alert(
            alert_id=alert_id, machine_id=machine.machine_id, line_id=machine.machine_id.split("-")[0],
            severity=_severity_for_score(result["risk_score"]),
            title=f"Elevated defect risk on {machine.machine_id} ({result['risk_score']}%)",
            description=f"Prediction Agent flagged {result['risk_level']} risk. {'; '.join(result['contributing_factors'][:3])}",
            trigger_data_json=json.dumps(result, default=str),
            source_agent="prediction_agent",
        )
        db.add(alert)
        db.flush()
        log_action(db, role="system", action_type="predict_alert", entity_type="alert",
                   entity_id=alert.alert_id, details=result, outcome="success", commit=False)
        created["alert"] = alert

        if result["risk_score"] >= DEFECT_THRESHOLD:
            defect_id = next_id(db, Defect, "defect_id", "DEF", 200000)
            defect = Defect(
                defect_id=defect_id, batch_id=None, machine_id=machine.machine_id, work_order_id=None,
                defect_type="dimension_deviation",  # predicted defects default to the most common process-linked type;
                                                     # refined once an inspection confirms the actual type
                location=machine.machine_id, detected_at=now, source=DefectSource.PREDICTION,
                risk_score=result["risk_score"], status=DefectStatus.OPEN,
            )
            db.add(defect)
            db.flush()
            alert.related_defect_id = defect.defect_id
            log_action(db, role="system", action_type="predict_defect", entity_type="defect",
                       entity_id=defect.defect_id, details=result, outcome="success", commit=False)
            created["defect"] = defect
            db.commit()
            rca, rec = run_rca_and_recommendation(db, defect)
            created["recommendation"] = rec
        else:
            db.commit()
    return {"score": result, "created": created}


def run_prediction_cycle(db: Session) -> list[dict]:
    """Scores every machine in the plant. Used by the polling endpoint
    the dashboard calls, and by the standalone simulation loop."""
    machines = db.query(Machine).all()
    return [run_prediction_for_machine(db, m) for m in machines]


def process_inspection(db: Session, inspection: Inspection) -> dict:
    """When a failed inspection is ingested, opens a Defect from it and
    runs RCA + Recommendation immediately (independent of the Prediction
    Agent's live-monitoring path)."""
    created = {"defect": None, "recommendation": None}
    if inspection.result != InspectionResult.FAIL:
        return created
    defect_id = next_id(db, Defect, "defect_id", "DEF", 200000)
    defect = Defect(
        defect_id=defect_id, batch_id=inspection.batch_id, machine_id=inspection.machine_id,
        work_order_id=None, defect_type=inspection.defect_type,
        location=inspection.defect_location or inspection.station,
        detected_at=inspection.timestamp, source=DefectSource.INSPECTION, risk_score=None,
        status=DefectStatus.OPEN,
    )
    db.add(defect)
    db.commit()
    db.refresh(defect)
    log_action(db, role="system", action_type="inspection_defect", entity_type="defect",
               entity_id=defect.defect_id, outcome="success")
    created["defect"] = defect
    rca, rec = run_rca_and_recommendation(db, defect)
    created["recommendation"] = rec
    return created


def score_and_persist_preprod(db: Session, work_order: WorkOrder, is_holdout: bool = False) -> PreProductionRiskScore:
    result = preprod_risk_agent.score_work_order(db, work_order)
    existing = db.query(PreProductionRiskScore).filter(
        PreProductionRiskScore.work_order_id == work_order.work_order_id
    ).first()
    if existing:
        existing.risk_score = result["risk_score"]
        existing.contributing_reasons_json = json.dumps(result["contributing_reasons"])
        existing.suggested_action = result["suggested_action"]
        existing.confidence = result["confidence"]
        row = existing
    else:
        row = PreProductionRiskScore(
            work_order_id=work_order.work_order_id, risk_score=result["risk_score"],
            contributing_reasons_json=json.dumps(result["contributing_reasons"]),
            suggested_action=result["suggested_action"], confidence=result["confidence"],
            status=RecommendationStatus.PENDING, is_holdout=is_holdout,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    log_action(db, role="system", action_type="preprod_score", entity_type="preprod_risk_score",
               entity_id=row.work_order_id, details=result, outcome="success")

    # Also raise a Recommendation entry so it flows through the same
    # Approve/Reject/Modify surface as every other agent output.
    existing_rec = db.query(Recommendation).filter(
        Recommendation.source_type == "preprod_batch", Recommendation.source_id == work_order.work_order_id
    ).first()
    if not existing_rec and result["risk_score"] >= 40:
        rec_id = next_id(db, Recommendation, "recommendation_id", "REC", 100000)
        rec = Recommendation(
            recommendation_id=rec_id, source_type="preprod_batch", source_id=work_order.work_order_id,
            action_text=result["suggested_action"],
            reasoning=f"Pre-production risk score {result['risk_score']}%. {'; '.join(result['contributing_reasons'][:3])}",
            confidence=result["confidence"], similar_case_ids_json=json.dumps([]),
            assigned_role="production", status=RecommendationStatus.PENDING,
        )
        db.add(rec)
        db.commit()
    return row


def run_preprod_scan(db: Session) -> list[PreProductionRiskScore]:
    """Scores every currently-scheduled (not-yet-run) work order."""
    scheduled = db.query(WorkOrder).filter(WorkOrder.status == WorkOrderStatus.SCHEDULED).all()
    return [score_and_persist_preprod(db, wo) for wo in scheduled]


def _persist_batch_quality_result(db: Session, result: dict) -> BatchQualityScore:
    """Writes an already-computed (and possibly LLM-enriched) score_batch
    result to the DB. Split out from scoring so the scan below can do
    all the slow LLM work in parallel first, then persist everything
    sequentially here - single-threaded DB access stays safe."""
    existing = db.query(BatchQualityScore).filter(BatchQualityScore.work_order_id == result["work_order_id"]).first()
    if existing:
        existing.quality_score = result["quality_score"]
        existing.risk_level = result["risk_level"]
        existing.probability_passing_qc = result["probability_passing_qc"]
        existing.confidence = result["confidence"]
        existing.contributing_factors_json = json.dumps(result["contributing_factors"])
        existing.sampling_rate_pct = result["sampling_rate_pct"]
        existing.sampling_rationale = result["sampling_rationale"]
        existing.ai_summary = result["ai_summary"]
        existing.llm_generated = result["llm_generated"]
        existing.computed_at = datetime.utcnow()
        row = existing
    else:
        row = BatchQualityScore(
            work_order_id=result["work_order_id"], batch_id=result["batch_id"], machine_id=result["machine_id"],
            quality_score=result["quality_score"], risk_level=result["risk_level"],
            probability_passing_qc=result["probability_passing_qc"], confidence=result["confidence"],
            contributing_factors_json=json.dumps(result["contributing_factors"]),
            sampling_rate_pct=result["sampling_rate_pct"], sampling_rationale=result["sampling_rationale"],
            ai_summary=result["ai_summary"], llm_generated=result["llm_generated"],
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    log_action(db, role="system", action_type="batch_quality_score", entity_type="batch_quality_score",
               entity_id=row.work_order_id, details=result, outcome="success")
    return row


def score_and_persist_batch_quality(db: Session, work_order: WorkOrder) -> BatchQualityScore:
    """Single-batch scoring, used where only one batch needs to be
    scored (e.g. on-demand from a detail page) - not the bulk scan."""
    result = batch_quality_agent.score_batch(db, work_order)
    return _persist_batch_quality_result(db, result)


def run_batch_quality_scan(db: Session, limit: int = 25) -> list[BatchQualityScore]:
    """
    Scores every currently in-progress or recently-completed work
    order - these are the batches a QA manager would actually care
    about right now. Scheduled (not-yet-run) batches are handled by the
    separate Pre-Production Risk Agent instead.

    Performance: the deterministic scoring (DB-bound) runs sequentially,
    but the LLM summary calls - which dominate wall-clock time, since
    each is a real network round-trip - run concurrently via a thread
    pool. Each LLM call is a pure function with no DB access, so this
    is safe without touching SQLAlchemy's session from multiple threads.
    """
    work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.in_([WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.COMPLETED]))
        .order_by(WorkOrder.planned_start.desc())
        .limit(limit)
        .all()
    )
    deterministic_results = [batch_quality_agent.score_batch_deterministic(db, wo) for wo in work_orders]

    if llm_service.is_configured():
        with ThreadPoolExecutor(max_workers=8) as executor:
            enriched_results = list(executor.map(batch_quality_agent.enrich_with_llm_summary, deterministic_results))
    else:
        # No LLM configured at all - skip the thread pool entirely rather
        # than spinning up 8 workers just to have each one immediately no-op.
        enriched_results = deterministic_results

    return [_persist_batch_quality_result(db, r) for r in enriched_results]


def handle_event(db: Session, machine: Machine, event_type: EventType, start_time, end_time, batch_id: str | None) -> dict:
    event_id = next_id(db, EventToUnitTrace, "event_id", "EVT", 8000)
    result = event_trace_agent.process_event(db, machine, event_type, start_time, end_time, event_id)

    trace = EventToUnitTrace(
        event_id=event_id, event_type=event_type, station=result["station"], machine_id=machine.machine_id,
        batch_id=result["batch_id"] or batch_id, start_time=start_time, end_time=end_time,
        affected_unit_start=result["affected_unit_start"], affected_unit_end=result["affected_unit_end"],
        affected_unit_count=result["affected_unit_count"], recommended_action=result["recommended_action"],
        status=EventTraceStatus.OPEN, recurrence_count=result["recurrence_count"],
    )
    db.add(trace)
    db.flush()

    # Immediate alert to the Operator (station-level) + Quality Manager scope
    alert_id = next_id(db, Alert, "alert_id", "ALT", 100000)
    severity = AlertSeverity.CRITICAL if event_type == EventType.FOREIGN_OBJECT_DETECTED else AlertSeverity.WARNING
    alert = Alert(
        alert_id=alert_id, machine_id=machine.machine_id, line_id=machine.machine_id.split("-")[0],
        severity=severity, title=f"{event_type.value.replace('_', ' ').title()} detected on {machine.machine_id}",
        description=result["recommended_action"],
        trigger_data_json=json.dumps(result, default=str), source_agent="event_trace_agent",
        related_event_id=event_id,
    )
    db.add(alert)
    db.flush()  # ensure this alert row is counted by next_id() before computing a second alert ID below

    log_action(db, role="system", action_type="event_trace", entity_type="event_trace",
               entity_id=event_id, details=result, outcome="success", commit=False)

    if result["recurrence_count"] >= 3:
        maint_alert_id = next_id(db, Alert, "alert_id", "ALT", 100000)
        maint_alert = Alert(
            alert_id=maint_alert_id, machine_id=machine.machine_id, line_id=machine.machine_id.split("-")[0],
            severity=AlertSeverity.WARNING,
            title=f"Recurring {event_type.value.replace('_', ' ')} events on {machine.machine_id}",
            description=(
                f"This is the {result['recurrence_count']}th {event_type.value.replace('_', ' ')} event on "
                f"{machine.machine_id} in the last 7 days - may indicate a mechanical issue rather than a one-off."
            ),
            trigger_data_json=json.dumps(result, default=str), source_agent="event_trace_agent",
            related_event_id=event_id,
        )
        db.add(maint_alert)

    db.commit()
    db.refresh(trace)
    return {"trace": trace, "result": result}
