"""
Builds the structured content for a shift/daily quality report. Shared
by the /api/reports router and the Chatbot Agent's "summarize today's
quality issues" query type, so both paths compute identical numbers
from the same query logic rather than duplicating it.
"""
from datetime import timedelta
from sqlalchemy.orm import Session

from backend.models.defect import Defect
from backend.models.alert import Alert
from backend.models.root_cause import RootCauseAnalysis
from backend.models.recommendation import Recommendation
from backend.models.batch_quality import BatchQualityScore
from backend.models.preprod_risk import PreProductionRiskScore
from backend.models.machine import Machine, Line
from backend.agents.prediction_agent import score_machine
import json


def build_report_content(db: Session, period_start, period_end, line_scope: str | None = None) -> dict:
    defect_query = db.query(Defect).filter(
        Defect.detected_at >= period_start, Defect.detected_at <= period_end
    )
    if line_scope:
        # Defects don't carry line_id directly; filter by machine_id prefix (machine_ids are "LINE#-...").
        defect_query = defect_query.filter(Defect.machine_id.like(f"{line_scope}-%"))
    defects = defect_query.all()

    defect_ids = [d.defect_id for d in defects]
    rcas = db.query(RootCauseAnalysis).filter(RootCauseAnalysis.defect_id.in_(defect_ids)).all() if defect_ids else []

    category_breakdown: dict[str, int] = {}
    for rca in rcas:
        category_breakdown[rca.primary_category] = category_breakdown.get(rca.primary_category, 0) + 1

    defect_type_counts: dict[str, int] = {}
    for d in defects:
        defect_type_counts[d.defect_type.value] = defect_type_counts.get(d.defect_type.value, 0) + 1
    top_root_causes = sorted(defect_type_counts.items(), key=lambda x: -x[1])[:5]

    alert_query = db.query(Alert).filter(Alert.created_at >= period_start, Alert.created_at <= period_end)
    if line_scope:
        alert_query = alert_query.filter(Alert.line_id == line_scope)
    alerts = alert_query.all()

    recs = (
        db.query(Recommendation)
        .filter(Recommendation.created_at >= period_start, Recommendation.created_at <= period_end)
        .filter(Recommendation.source_id.in_(defect_ids)) if defect_ids else []
    )
    recs = recs.all() if hasattr(recs, "all") else recs
    actions_taken = [
        {"recommendation_id": r.recommendation_id, "action": r.action_text, "status": r.status.value}
        for r in recs if r.status.value in ("approved", "modified")
    ]

    # Trend vs previous period of equal length
    period_length = period_end - period_start
    prev_start = period_start - period_length
    prev_end = period_start
    prev_defect_count = (
        db.query(Defect)
        .filter(Defect.detected_at >= prev_start, Defect.detected_at < prev_end)
        .count()
    )
    current_count = len(defects)
    if prev_defect_count > 0:
        trend_pct = round((current_count - prev_defect_count) / prev_defect_count * 100, 1)
    else:
        trend_pct = None

    # Full recommendation-decision picture for the period, not just the
    # approved/modified subset - a report should show what's still
    # pending and the actual approval rate, not only "wins".
    all_recs = (
        db.query(Recommendation)
        .filter(Recommendation.created_at >= period_start, Recommendation.created_at <= period_end)
        .filter(Recommendation.source_id.in_(defect_ids)) if defect_ids else []
    )
    all_recs = all_recs.all() if hasattr(all_recs, "all") else all_recs
    rec_approved = len([r for r in all_recs if r.status.value == "approved"])
    rec_rejected = len([r for r in all_recs if r.status.value == "rejected"])
    rec_modified = len([r for r in all_recs if r.status.value == "modified"])
    rec_pending = len([r for r in all_recs if r.status.value == "pending"])
    rec_decided = rec_approved + rec_rejected + rec_modified
    recommendations_summary = {
        "total": len(all_recs), "approved": rec_approved, "rejected": rec_rejected,
        "modified": rec_modified, "pending": rec_pending,
        "approval_rate_pct": round((rec_approved + rec_modified) / rec_decided * 100, 1) if rec_decided else None,
    }

    # Batch quality scores computed during this period - the composite,
    # QA-manager-facing view, distinct from the raw defect count above.
    bq_query = db.query(BatchQualityScore).filter(
        BatchQualityScore.computed_at >= period_start, BatchQualityScore.computed_at <= period_end
    )
    if line_scope:
        bq_query = bq_query.filter(BatchQualityScore.machine_id.like(f"{line_scope}-%"))
    bq_rows = bq_query.all()
    worst_batches = sorted(bq_rows, key=lambda r: r.quality_score)[:5]
    batch_quality_summary = {
        "scored_count": len(bq_rows),
        "avg_quality_score": round(sum(r.quality_score for r in bq_rows) / len(bq_rows), 1) if bq_rows else None,
        "high_risk_count": len([r for r in bq_rows if r.risk_level == "high"]),
        "worst_batches": [
            {"work_order_id": r.work_order_id, "batch_id": r.batch_id, "quality_score": r.quality_score, "risk_level": r.risk_level}
            for r in worst_batches
        ],
    }

    # Pre-production risk scans from this period - batches flagged
    # before they even ran, and whether a human has reviewed the flag.
    pp_query = db.query(PreProductionRiskScore).filter(
        PreProductionRiskScore.created_at >= period_start, PreProductionRiskScore.created_at <= period_end
    )
    pp_rows = pp_query.all()
    preprod_summary = {
        "scanned_count": len(pp_rows),
        "avg_risk_score": round(sum(r.risk_score for r in pp_rows) / len(pp_rows), 1) if pp_rows else None,
        "high_risk_count": len([r for r in pp_rows if r.risk_score >= 66]),
        "pending_review_count": len([r for r in pp_rows if r.status.value == "pending"]),
    }

    # Live machine risk snapshot - "as of right now", not period-bound,
    # so the report also answers "what does the floor look like today",
    # not just "what happened in the window". Kept small and best-effort:
    # scoring every machine can be slow with many machines, and a report
    # shouldn't fail to generate over this section.
    machine_snapshot = []
    try:
        machine_query = db.query(Machine)
        if line_scope:
            line = db.query(Line).filter(Line.line_code == line_scope).first()
            if line:
                machine_query = machine_query.filter(Machine.line_id == line.id)
        machines = machine_query.all()
        scored = sorted((score_machine(db, m) for m in machines), key=lambda s: -s["risk_score"])
        machine_snapshot = [
            {"machine_id": s["machine_id"], "risk_score": s["risk_score"], "risk_level": s["risk_level"]}
            for s in scored[:5]
        ]
    except Exception:
        machine_snapshot = []

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "defects_found": current_count,
        "categorization_breakdown": category_breakdown,
        "top_root_causes": [{"defect_type": k, "count": v} for k, v in top_root_causes],
        "alerts_raised": len(alerts),
        "alerts_by_severity": {
            sev: len([a for a in alerts if a.severity.value == sev]) for sev in ("info", "warning", "critical")
        },
        "actions_taken": actions_taken,
        "recommendations_summary": recommendations_summary,
        "batch_quality_summary": batch_quality_summary,
        "preprod_summary": preprod_summary,
        "machine_snapshot": machine_snapshot,
        "defect_trend_vs_previous_period_pct": trend_pct,
        "previous_period_defect_count": prev_defect_count,
    }
