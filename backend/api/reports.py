import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.report import Report
from backend.models.enums import ReportType, ReportStatus, UserRole
from backend.auth.dependencies import get_current_user, require_roles, CurrentUser
from backend.schemas.report import GenerateReportRequest, ReportOut, UpdateReportRequest
from backend.services.report_service import build_report_content
from backend.services.rbac_scope import line_scope_for_user, is_plant_wide
from backend.services.id_gen import next_id
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _to_out(r: Report) -> ReportOut:
    return ReportOut(
        report_id=r.report_id, report_type=r.report_type.value, period_start=r.period_start,
        period_end=r.period_end, generated_by=r.generated_by, content=json.loads(r.content_json),
        status=r.status.value, finalized_by=r.finalized_by, finalized_at=r.finalized_at, created_at=r.created_at,
    )


@router.get("", response_model=list[ReportOut])
def list_reports(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    reports = db.query(Report).order_by(Report.created_at.desc()).limit(100).all()
    return [_to_out(r) for r in reports]


@router.post("/generate", response_model=ReportOut)
def generate_report(
    payload: GenerateReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Auto-generates a draft report. Any authenticated role can generate
    one for their own review; only Quality Manager can mark it final."""
    if payload.period_end <= payload.period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start.")
    line_scope = line_scope_for_user(current_user)
    content = build_report_content(db, payload.period_start, payload.period_end, line_scope=line_scope)
    report_id = next_id(db, Report, "report_id", "RPT", 100000)
    report = Report(
        report_id=report_id, report_type=ReportType(payload.report_type), period_start=payload.period_start,
        period_end=payload.period_end, generated_by=current_user.username, content_json=json.dumps(content),
        status=ReportStatus.DRAFT,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    log_action(
        db, role=current_user.role.value, action_type="generate_report", entity_type="report",
        entity_id=report.report_id, user_id=current_user.id, username=current_user.username, outcome="success",
    )
    return _to_out(report)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    return _to_out(report)


@router.patch("/{report_id}", response_model=ReportOut)
def update_report(
    report_id: str,
    payload: UpdateReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reports are reviewable/editable by a human before being marked
    final; only Quality Manager (or Plant Head/Admin) may finalize."""
    report = db.query(Report).filter(Report.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    if report.status == ReportStatus.FINAL:
        raise HTTPException(status_code=400, detail="Report is already finalized and cannot be edited.")

    if payload.content is not None:
        report.content_json = json.dumps(payload.content)

    if payload.finalize:
        if current_user.role not in (UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN):
            raise HTTPException(status_code=403, detail="Only Quality Manager, Plant Head, or Admin may finalize a report.")
        report.status = ReportStatus.FINAL
        report.finalized_by = current_user.username
        from datetime import datetime
        report.finalized_at = datetime.utcnow()

    db.commit()
    db.refresh(report)
    log_action(
        db, role=current_user.role.value, action_type="finalize_report" if payload.finalize else "edit_report",
        entity_type="report", entity_id=report.report_id, user_id=current_user.id,
        username=current_user.username, outcome=report.status.value,
    )
    return _to_out(report)


@router.get("/{report_id}/export")
def export_report(report_id: str, format: str = "html", current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
    content = json.loads(report.content_json)

    if format == "csv":
        rs = content.get("recommendations_summary", {})
        bq = content.get("batch_quality_summary", {})
        pp = content.get("preprod_summary", {})
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["QualityGuard AI Report", report.report_id, report.report_type.value, report.status.value])
        writer.writerow(["Period", str(report.period_start), str(report.period_end)])
        writer.writerow([])
        writer.writerow(["Defects Found", content.get("defects_found")])
        writer.writerow(["Alerts Raised", content.get("alerts_raised")])
        writer.writerow(["Defect Trend vs Previous Period (%)", content.get("defect_trend_vs_previous_period_pct")])
        writer.writerow([])
        writer.writerow(["Root Cause Category", "Count"])
        for k, v in content.get("categorization_breakdown", {}).items():
            writer.writerow([k, v])
        writer.writerow([])
        writer.writerow(["Top Defect Types", "Count"])
        for row in content.get("top_root_causes", []):
            writer.writerow([row["defect_type"], row["count"]])
        writer.writerow([])
        writer.writerow(["Recommendations", "Count"])
        writer.writerow(["Approved", rs.get("approved", 0)])
        writer.writerow(["Modified", rs.get("modified", 0)])
        writer.writerow(["Rejected", rs.get("rejected", 0)])
        writer.writerow(["Still Pending", rs.get("pending", 0)])
        writer.writerow(["Approval Rate (%)", rs.get("approval_rate_pct")])
        writer.writerow([])
        writer.writerow(["Actions Taken", "Status"])
        for a in content.get("actions_taken", []):
            writer.writerow([a["action"], a["status"]])
        writer.writerow([])
        writer.writerow(["Batch Quality", "Value"])
        writer.writerow(["Batches Scored", bq.get("scored_count", 0)])
        writer.writerow(["Average Quality Score", bq.get("avg_quality_score")])
        writer.writerow(["High Risk Batches", bq.get("high_risk_count", 0)])
        writer.writerow([])
        writer.writerow(["Work Order", "Batch", "Quality Score", "Risk Level"])
        for b in bq.get("worst_batches", []):
            writer.writerow([b["work_order_id"], b["batch_id"], b["quality_score"], b["risk_level"]])
        writer.writerow([])
        writer.writerow(["Pre-Production Risk", "Value"])
        writer.writerow(["Batches Scanned", pp.get("scanned_count", 0)])
        writer.writerow(["Average Risk Score", pp.get("avg_risk_score")])
        writer.writerow(["High Risk Flagged", pp.get("high_risk_count", 0)])
        writer.writerow(["Awaiting Review", pp.get("pending_review_count", 0)])
        writer.writerow([])
        writer.writerow(["Machine (current)", "Risk %", "Risk Level"])
        for m in content.get("machine_snapshot", []):
            writer.writerow([m["machine_id"], m["risk_score"], m["risk_level"]])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f"attachment; filename={report.report_id}.csv"
        })

    # default: HTML
    rs = content.get("recommendations_summary", {})
    bq = content.get("batch_quality_summary", {})
    pp = content.get("preprod_summary", {})
    rows_cat = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in content.get("categorization_breakdown", {}).items())
    rows_causes = "".join(f"<tr><td>{r['defect_type']}</td><td>{r['count']}</td></tr>" for r in content.get("top_root_causes", []))
    rows_actions = "".join(f"<tr><td>{a['action']}</td><td>{a['status']}</td></tr>" for a in content.get("actions_taken", []))
    rows_worst_batches = "".join(
        f"<tr><td>{b['work_order_id']}</td><td>{b['batch_id']}</td><td>{b['quality_score']}/100</td><td>{b['risk_level']}</td></tr>"
        for b in bq.get("worst_batches", [])
    )
    rows_snapshot = "".join(
        f"<tr><td>{m['machine_id']}</td><td>{m['risk_score']}%</td><td>{m['risk_level']}</td></tr>"
        for m in content.get("machine_snapshot", [])
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{report.report_id}</title>
    <style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;color:#1a1a2e}}
    table{{width:100%;border-collapse:collapse;margin:12px 0}} td,th{{border:1px solid #ddd;padding:8px;text-align:left}}
    h1{{color:#0f3460}} .badge{{display:inline-block;padding:2px 10px;border-radius:12px;background:#e94560;color:#fff;font-size:12px}}</style>
    </head><body>
    <h1>QualityGuard AI - {report.report_type.value.title()} Report</h1>
    <p><span class="badge">{report.status.value.upper()}</span> {report.report_id} | {report.period_start} to {report.period_end}</p>
    <p>Generated by {report.generated_by}</p>
    <h2>Summary</h2>
    <p>Defects found: <b>{content.get('defects_found')}</b> | Alerts raised: <b>{content.get('alerts_raised')}</b> |
    Trend vs previous period: <b>{content.get('defect_trend_vs_previous_period_pct')}%</b> |
    Recommendation approval rate: <b>{rs.get('approval_rate_pct', '—')}%</b></p>
    <h2>Root Cause Categorization</h2><table><tr><th>Category</th><th>Count</th></tr>{rows_cat}</table>
    <h2>Top Defect Types</h2><table><tr><th>Defect Type</th><th>Count</th></tr>{rows_causes}</table>
    <h2>Recommendations This Period</h2>
    <table><tr><th>Approved</th><th>Modified</th><th>Rejected</th><th>Pending</th></tr>
    <tr><td>{rs.get('approved', 0)}</td><td>{rs.get('modified', 0)}</td><td>{rs.get('rejected', 0)}</td><td>{rs.get('pending', 0)}</td></tr></table>
    <h2>Actions Taken</h2><table><tr><th>Action</th><th>Status</th></tr>{rows_actions}</table>
    <h2>Batch Quality</h2>
    <p>{bq.get('scored_count', 0)} batch(es) scored, averaging {bq.get('avg_quality_score', '—')}/100 - {bq.get('high_risk_count', 0)} flagged high risk.</p>
    <table><tr><th>Work Order</th><th>Batch</th><th>Score</th><th>Risk</th></tr>{rows_worst_batches}</table>
    <h2>Pre-Production Risk</h2>
    <table><tr><th>Scanned</th><th>Avg Risk</th><th>High Risk</th><th>Awaiting Review</th></tr>
    <tr><td>{pp.get('scanned_count', 0)}</td><td>{pp.get('avg_risk_score', '—')}%</td><td>{pp.get('high_risk_count', 0)}</td><td>{pp.get('pending_review_count', 0)}</td></tr></table>
    <h2>Machine Risk Snapshot (as of export)</h2>
    <table><tr><th>Machine</th><th>Risk</th><th>Level</th></tr>{rows_snapshot}</table>
    </body></html>"""
    return PlainTextResponse(html, media_type="text/html")
