"""
Operator quality signals - factored out here because both the Batch
Quality Agent and the (future) Operator Quality Intelligence feature
need the same underlying computation: how has this operator's/shift's
work historically inspected, in terms of defect rate.
"""
from sqlalchemy.orm import Session

from backend.models.production import ProductionLog
from backend.models.inspection import Inspection


def operator_defect_rate(db: Session, operator_username: str) -> dict:
    """
    Returns {"defect_rate": float|None, "inspections": int, "batches": int}
    for the given operator, based on inspection outcomes across every
    batch that operator's production logs are attached to. Returns
    defect_rate=None when there isn't enough inspection history to
    compute a meaningful rate.
    """
    logs = db.query(ProductionLog).filter(ProductionLog.operator_username == operator_username).all()
    if not logs:
        return {"defect_rate": None, "inspections": 0, "batches": 0}
    batch_ids = list({l.batch_id for l in logs})
    total = db.query(Inspection).filter(Inspection.batch_id.in_(batch_ids)).count()
    if total == 0:
        return {"defect_rate": None, "inspections": 0, "batches": len(batch_ids)}
    fails = db.query(Inspection).filter(Inspection.batch_id.in_(batch_ids), Inspection.result == "fail").count()
    return {"defect_rate": round(fails / total, 4), "inspections": total, "batches": len(batch_ids)}


def operator_quality_component(db: Session, operator_username: str) -> tuple[float, str | None]:
    """
    Returns (risk_component 0-1 where higher=worse, explanatory factor
    string or None if the rate isn't notably elevated / there's no data).
    Used as one weighted input into the Batch Quality Score.
    """
    if not operator_username:
        return 0.0, None
    stats = operator_defect_rate(db, operator_username)
    if stats["defect_rate"] is None:
        return 0.0, None
    rate = stats["defect_rate"]
    component = min(1.0, rate * 2.5)
    factor = None
    if rate > 0.10:
        factor = f"operator {operator_username} has a historical inspection failure rate of {rate:.0%} across {stats['inspections']} inspections"
    return component, factor


TRAINING_THRESHOLD = 0.20  # defect rate above this flags an operator for refresher training
FATIGUE_GAP_THRESHOLD = 5.0  # percentage-point gap between best/worst shift pass rate worth calling out


def all_operator_stats(db: Session) -> list[dict]:
    """
    Per-operator rollup across every operator who appears in production
    logs: defect rate, inspection/batch counts, and a training-needed
    flag. Sorted worst-first so the people who'd benefit most from
    attention surface at the top.
    """
    operator_rows = db.query(ProductionLog.operator_username).distinct().all()
    results = []
    for (operator,) in operator_rows:
        if not operator:
            continue
        stats = operator_defect_rate(db, operator)
        if stats["defect_rate"] is None:
            continue
        results.append({
            "operator": operator,
            "defect_rate_pct": round(stats["defect_rate"] * 100, 1),
            "inspections": stats["inspections"],
            "batches": stats["batches"],
            "training_recommended": stats["defect_rate"] > TRAINING_THRESHOLD,
        })
    return sorted(results, key=lambda r: r["defect_rate_pct"], reverse=True)


def shift_stats(db: Session) -> list[dict]:
    """Per-shift pass-rate rollup (Morning/Afternoon/Night), used both
    to display shift performance and to detect a fatigue-pattern gap
    between shifts."""
    results = []
    for shift in ["morning", "afternoon", "night"]:
        logs = db.query(ProductionLog).filter(ProductionLog.shift == shift).all()
        if not logs:
            continue
        batch_ids = list({l.batch_id for l in logs})
        total = db.query(Inspection).filter(Inspection.batch_id.in_(batch_ids)).count()
        if total == 0:
            continue
        fails = db.query(Inspection).filter(Inspection.batch_id.in_(batch_ids), Inspection.result == "fail").count()
        results.append({
            "shift": shift,
            "pass_rate_pct": round((1 - fails / total) * 100, 1),
            "inspections": total,
            "batches": len(batch_ids),
        })
    return sorted(results, key=lambda r: -r["pass_rate_pct"])


def generate_insights(operator_rows: list[dict], shift_rows: list[dict]) -> list[str]:
    """
    Plain, disclosed pattern-matching over the already-computed stats
    above - not an LLM call. Kept deterministic since these insights
    inform real HR/training decisions and should be exactly reproducible
    from the underlying numbers, same reasoning as Intelligent Sampling.
    """
    insights = []
    if len(shift_rows) >= 2:
        best, worst = shift_rows[0], shift_rows[-1]
        gap = best["pass_rate_pct"] - worst["pass_rate_pct"]
        if gap > FATIGUE_GAP_THRESHOLD:
            insights.append(
                f"{worst['shift'].capitalize()} shift's pass rate ({worst['pass_rate_pct']}%) is {gap:.1f} points "
                f"below {best['shift']} shift ({best['pass_rate_pct']}%) - worth investigating for fatigue, "
                f"staffing, or handoff-quality causes rather than assuming it's purely operator skill."
            )
    flagged = [r for r in operator_rows if r["training_recommended"]]
    if flagged:
        names = ", ".join(r["operator"] for r in flagged[:4])
        insights.append(
            f"{len(flagged)} operator(s) show a defect rate above {TRAINING_THRESHOLD:.0%} and may benefit from "
            f"refresher training or a fixture/checklist aid: {names}."
        )
    if not insights:
        insights.append("No significant operator or shift performance gaps detected in the current data.")
    return insights
