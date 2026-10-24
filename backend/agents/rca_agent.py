"""
RCA (Root Cause + Categorization + Responsibility) Agent
=========================================================
For a given defect, produces a confidence-weighted split across the
four root-cause categories (Equipment, Material, Process, Human)
rather than forcing a single label, then routes the defect to the
owning role for ACTION - framed as routing, never blame assignment.

Method: each defect_type carries a documented prior distribution over
categories (reflecting typical causes for that defect type in
manufacturing), which is then adjusted by actual evidence pulled from
this specific defect's data (recent sensor anomalies + maintenance
status for Equipment; material lot quality/contamination history for
Material; shift-changeover/process-parameter signals for Process;
changeover/shift signals for Human). The adjusted weights are
renormalized to sum to 1.0. This keeps every number traceable to
either a documented prior or a specific queried fact - never a bare
black-box output.
"""
from datetime import timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from backend.models.defect import Defect
from backend.models.sensor import SensorReading
from backend.models.machine import Machine
from backend.models.mes import WorkOrder, MaterialLot
from backend.models.production import ProductionLog
from backend.auth.dependencies import CATEGORY_TO_ROLE
from backend.services import llm_service

# Documented prior: typical root-cause distribution by defect type,
# drawn from general manufacturing quality-engineering heuristics.
DEFECT_TYPE_PRIORS = {
    "scratch":                 {"human": 0.50, "process": 0.20, "equipment": 0.20, "material": 0.10},
    "dimension_deviation":     {"process": 0.50, "equipment": 0.30, "material": 0.10, "human": 0.10},
    "weld_crack":              {"equipment": 0.50, "process": 0.30, "material": 0.10, "human": 0.10},
    "surface_contamination":   {"material": 0.55, "process": 0.20, "equipment": 0.15, "human": 0.10},
    "misalignment":            {"process": 0.45, "equipment": 0.35, "human": 0.15, "material": 0.05},
    "porosity":                {"equipment": 0.45, "material": 0.30, "process": 0.20, "human": 0.05},
    "discoloration":           {"material": 0.50, "process": 0.25, "equipment": 0.15, "human": 0.10},
    "incomplete_assembly":     {"human": 0.55, "process": 0.25, "equipment": 0.10, "material": 0.10},
}
DEFAULT_PRIOR = {"equipment": 0.25, "material": 0.25, "process": 0.25, "human": 0.25}

CATEGORIES = ["equipment", "material", "process", "human"]


def _equipment_evidence(db: Session, machine: Optional[Machine], defect: Defect) -> tuple[float, list[str]]:
    if machine is None:
        return 0.0, []
    window_start = defect.detected_at - timedelta(hours=6)
    readings = (
        db.query(SensorReading)
        .filter(
            SensorReading.machine_id == machine.machine_id,
            SensorReading.timestamp >= window_start,
            SensorReading.timestamp <= defect.detected_at,
        )
        .all()
    )
    factors = []
    evidence = 0.0
    if readings:
        present = [r for r in readings if r.value is not None]
        anomalous = [r for r in present if r.is_anomalous or r.data_quality_flag.value != "ok"]
        if present:
            ratio = len(anomalous) / len(present)
            evidence += min(1.0, ratio * 1.5)
            if anomalous:
                factors.append(f"{len(anomalous)}/{len(present)} sensor readings on {machine.machine_id} were anomalous in the 6h before detection")
    if machine.next_maintenance_due and defect.detected_at > machine.next_maintenance_due:
        overdue_days = (defect.detected_at - machine.next_maintenance_due).total_seconds() / 86400
        evidence += min(0.6, overdue_days / 30.0)
        factors.append(f"machine was {overdue_days:.0f} day(s) overdue for maintenance at time of detection")
    return min(1.0, evidence), factors


def _material_evidence(db: Session, work_order: Optional[WorkOrder]) -> tuple[float, list[str]]:
    if work_order is None:
        return 0.0, []
    lot = db.query(MaterialLot).filter(MaterialLot.lot_id == work_order.material_lot_id).first()
    if lot is None:
        return 0.0, []
    factors = []
    evidence = max(0.0, (90.0 - lot.quality_score) / 60.0)
    if lot.prior_contamination_defects > 0:
        evidence += min(0.5, lot.prior_contamination_defects * 0.15)
        factors.append(f"material lot {lot.lot_id} ({lot.supplier_name}) linked to {lot.prior_contamination_defects} prior contamination defect(s)")
    if lot.quality_score < 75:
        factors.append(f"material lot {lot.lot_id} quality score is {lot.quality_score}/100 (below typical threshold of 75)")
    return min(1.0, evidence), factors


def _process_and_human_evidence(db: Session, defect: Defect) -> tuple[float, float, list[str], list[str]]:
    prod_logs = (
        db.query(ProductionLog)
        .filter(ProductionLog.batch_id == defect.batch_id)
        .all() if defect.batch_id else []
    )
    process_evidence, human_evidence = 0.0, 0.0
    process_factors, human_factors = [], []
    for log in prod_logs:
        if log.changeover_flag:
            process_evidence += 0.35
            human_evidence += 0.30
            process_factors.append(f"batch {log.batch_id} was produced immediately after a line changeover")
            human_factors.append(f"batch {log.batch_id} was produced immediately after a line changeover (shift: {log.shift.value})")
        if log.units_scrapped and log.units_produced:
            scrap_rate = log.units_scrapped / max(1, log.units_produced)
            if scrap_rate > 0.03:
                process_evidence += min(0.4, scrap_rate * 3)
                process_factors.append(f"batch scrap rate was {scrap_rate:.1%} on this production segment")
    return min(1.0, process_evidence), min(1.0, human_evidence), process_factors, human_factors


def _llm_explanation(defect, defect_type, primary_category, category_split, factors, routed_role):
    """
    Asks Claude to phrase the already-computed RCA result as a natural
    paragraph. Every number here (category_split, primary_category) was
    computed above by the deterministic prior+evidence math - Claude is
    only told to write about them, never to decide them. Returns None
    if the LLM is unavailable/fails, so the caller falls back to the
    rule-based explanation.
    """
    facts = (
        f"Defect type: {defect_type}\n"
        f"Machine: {defect.machine_id}\n"
        f"Location: {defect.location}\n"
        f"Root-cause category split (already computed, do not change): "
        f"{', '.join(f'{k}: {v:.0%}' for k, v in category_split.items())}\n"
        f"Primary category: {primary_category}\n"
        f"Routed to: {routed_role} team\n"
        f"Contributing evidence found:\n" + "\n".join(f"- {f}" for f in factors)
    )
    user_prompt = (
        f"{facts}\n\n"
        f"Write a 2-3 sentence explanation of this root-cause analysis for a quality manager reviewing it. "
        f"State the primary category and why, referencing the specific evidence above. Do not restate every "
        f"percentage - focus on making the reasoning clear."
    )
    return llm_service.generate(llm_service.GROUNDING_RULE, user_prompt, max_tokens=220)


def analyze_defect(db: Session, defect: Defect) -> dict:
    prior = DEFECT_TYPE_PRIORS.get(defect.defect_type.value, DEFAULT_PRIOR)
    machine = db.query(Machine).filter(Machine.machine_id == defect.machine_id).first()
    work_order = (
        db.query(WorkOrder).filter(WorkOrder.batch_id == defect.batch_id).first()
        if defect.batch_id else None
    )

    equipment_ev, equipment_factors = _equipment_evidence(db, machine, defect)
    material_ev, material_factors = _material_evidence(db, work_order)
    process_ev, human_ev, process_factors, human_factors = _process_and_human_evidence(db, defect)

    evidence = {"equipment": equipment_ev, "material": material_ev, "process": process_ev, "human": human_ev}
    all_factors = equipment_factors + material_factors + process_factors + human_factors

    # Adjust prior by evidence: weight = prior * (1 + evidence), then renormalize.
    adjusted = {cat: prior.get(cat, 0.0) * (1.0 + evidence[cat]) for cat in CATEGORIES}
    total = sum(adjusted.values()) or 1.0
    category_split = {cat: round(adjusted[cat] / total, 3) for cat in CATEGORIES}
    # fix rounding drift so the split sums to exactly 1.0
    drift = round(1.0 - sum(category_split.values()), 3)
    top_cat = max(category_split, key=category_split.get)
    category_split[top_cat] = round(category_split[top_cat] + drift, 3)

    primary_category = max(category_split, key=category_split.get)
    routed_role = CATEGORY_TO_ROLE[primary_category].value

    data_points = len(all_factors)
    confidence = round(float(np.clip(0.4 + 0.12 * data_points, 0.4, 0.95)), 2)

    if not all_factors:
        all_factors = [f"no strong corroborating signals found; category split based on documented priors for '{defect.defect_type.value}' defects"]

    # The rule-based explanation is ALWAYS computed first and is what
    # gets returned if the LLM is unavailable/fails - it's a complete,
    # correct explanation on its own, never a degraded placeholder.
    rule_based_explanation = (
        f"Defect type '{defect.defect_type.value}' typically originates from "
        f"{', '.join(f'{k} ({v:.0%})' for k, v in sorted(prior.items(), key=lambda x: -x[1]))} in general manufacturing "
        f"experience. For this specific case, evidence points most strongly to "
        f"{primary_category} ({category_split[primary_category]:.0%} confidence-weighted)."
    )

    explanation, llm_generated = rule_based_explanation, False
    llm_text = _llm_explanation(defect, defect.defect_type.value, primary_category, category_split, all_factors, routed_role)
    if llm_text:
        explanation, llm_generated = llm_text, True

    return {
        "defect_id": defect.defect_id,
        "category_split": category_split,
        "primary_category": primary_category,
        "contributing_factors": all_factors,
        "explanation": explanation,
        "confidence": confidence,
        "routed_role": routed_role,
        "llm_generated": llm_generated,
    }
