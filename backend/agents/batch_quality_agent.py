"""
Batch Quality Agent
=====================
Computes the AI Batch Quality Score - the primary dashboard metric QA
managers see first. Rather than a single machine's live risk, this is a
composite view of one specific batch, combining:

  - Sensor data / process stability  (reuses the Prediction Agent's
    rule+ML risk score for the batch's assigned machine)
  - Machine health & maintenance history (reuses the same helper the
    Pre-Production Risk Agent uses)
  - Raw material quality (reuses the same material-lot helper)
  - Operator history (new: historical inspection failure rate for the
    operator who ran this batch)
  - Environmental conditions (new: humidity readings specifically, as
    the most process-sensitive environmental signal in this plant)

Every weight is disclosed and every factor is traceable to a specific
queried fact, matching the explainability pattern used by every other
agent in this app. A Claude-generated natural-language summary is
layered on top purely for readability - it is asked to describe the
already-computed numbers, never to compute them itself, and the app
falls back to a plain deterministic sentence if the LLM is unavailable.
"""
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from backend.models.mes import WorkOrder, MaterialLot
from backend.models.machine import Machine
from backend.models.production import ProductionLog
from backend.models.sensor import SensorReading
from backend.data_generator.config import SENSOR_NORMAL_RANGES
from backend.agents.prediction_agent import score_machine
from backend.agents.preprod_risk_agent import _material_component, _machine_health_component
from backend.services.operator_signals import operator_quality_component
from backend.services.intelligent_sampling import recommend_sampling_rate
from backend.services import llm_service

# Disclosed weights - sum to 1.0. Sensor/process stability carries the
# most weight since it reflects the most recent, highest-resolution data.
WEIGHT_SENSOR = 0.30
WEIGHT_MACHINE_HEALTH = 0.20
WEIGHT_MATERIAL = 0.20
WEIGHT_OPERATOR = 0.15
WEIGHT_ENVIRONMENTAL = 0.15


def _environmental_component(db: Session, machine_id: str, reference_time: datetime, window_hours: int = 6) -> tuple[float, Optional[str]]:
    """Humidity-specific check - called out separately from the general
    sensor/process-stability score because humidity is the most
    process-sensitive environmental factor for this plant's coating/cure
    operations, and the spec asks for it as its own factor."""
    window_start = reference_time - timedelta(hours=window_hours)
    readings = (
        db.query(SensorReading)
        .filter(
            SensorReading.machine_id == machine_id, SensorReading.sensor_type == "humidity",
            SensorReading.timestamp >= window_start, SensorReading.timestamp <= reference_time,
            SensorReading.value.isnot(None),
        )
        .all()
    )
    if not readings:
        return 0.0, None
    lo, hi = SENSOR_NORMAL_RANGES["humidity"]
    out_of_range = [r for r in readings if r.value < lo or r.value > hi]
    ratio = len(out_of_range) / len(readings)
    factor = None
    if ratio > 0.1:
        factor = f"humidity out of range ({lo}-{hi}%RH) in {len(out_of_range)}/{len(readings)} recent readings ({ratio:.0%})"
    return ratio, factor


def _get_operator_for_work_order(db: Session, work_order: WorkOrder) -> Optional[str]:
    log = (
        db.query(ProductionLog)
        .filter(ProductionLog.work_order_id == work_order.work_order_id)
        .order_by(ProductionLog.start_time.asc())
        .first()
    )
    return log.operator_username if log else None


def _llm_summary(work_order_id: str, quality_score: float, risk_level: str, probability: float,
                  confidence: float, factors: list[str]) -> Optional[str]:
    facts = (
        f"Batch/work order: {work_order_id}\n"
        f"Quality Score (already computed, 0-100, higher is better): {quality_score}\n"
        f"Risk level (already computed): {risk_level}\n"
        f"Probability of passing QC (already computed): {probability}%\n"
        f"Confidence (already computed): {confidence:.0%}\n"
        f"Contributing factors found:\n" + ("\n".join(f"- {f}" for f in factors) if factors else "- no elevated risk factors found")
    )
    user_prompt = (
        f"{facts}\n\nWrite a 1-2 sentence summary of this batch's quality outlook for a QA manager's dashboard "
        f"card. Reference the specific factors above if there are any notable ones. Do not restate every number - "
        f"the score is already shown separately; focus on the 'why'."
    )
    return llm_service.generate(llm_service.GROUNDING_RULE, user_prompt, max_tokens=140)


def score_batch_deterministic(db: Session, work_order: WorkOrder) -> dict:
    """
    Computes everything EXCEPT the LLM summary. Split out from
    score_batch() so a bulk scan can run this part sequentially (it
    needs the DB session) while parallelizing the slow LLM calls
    afterward via enrich_with_llm_summary(), which touches no DB at all.
    """
    reference_time = datetime.utcnow()
    machine = db.query(Machine).filter(Machine.machine_id == work_order.machine_id).first()
    lot = db.query(MaterialLot).filter(MaterialLot.lot_id == work_order.material_lot_id).first()
    operator = _get_operator_for_work_order(db, work_order)

    factors: list[str] = []

    # 1. Sensor data / process stability - reuse the Prediction Agent's
    #    live rule+ML risk score for this batch's machine.
    sensor_component = 0.0
    if machine:
        pred = score_machine(db, machine)
        sensor_component = pred["risk_score"] / 100.0
        if pred["risk_level"] != "low":
            factors.append(f"live machine risk on {machine.machine_id} is {pred['risk_score']}% ({pred['risk_level']})")

    # 2. Machine health & maintenance history
    machine_health_component, machine_factors = _machine_health_component(db, machine, reference_time)
    factors.extend(machine_factors)

    # 3. Raw material quality
    material_component, material_factors = _material_component(db, lot)
    factors.extend(material_factors)

    # 4. Operator history
    operator_component, operator_factor = operator_quality_component(db, operator) if operator else (0.0, None)
    if operator_factor:
        factors.append(operator_factor)

    # 5. Environmental conditions (humidity)
    environmental_component, env_factor = _environmental_component(db, work_order.machine_id, reference_time)
    if env_factor:
        factors.append(env_factor)

    combined_risk = (
        WEIGHT_SENSOR * sensor_component
        + WEIGHT_MACHINE_HEALTH * machine_health_component
        + WEIGHT_MATERIAL * material_component
        + WEIGHT_OPERATOR * operator_component
        + WEIGHT_ENVIRONMENTAL * environmental_component
    )
    combined_risk = float(np.clip(combined_risk, 0.0, 1.0))

    quality_score = round((1.0 - combined_risk) * 100, 1)
    # Probability of passing QC is deliberately not identical to quality_score:
    # it uses a slightly convex curve so genuinely low-risk batches read as
    # very likely to pass, while the quality_score stays a more literal
    # 1-minus-risk figure. Both are disclosed, deterministic transforms of
    # the same combined_risk value - not independent guesses.
    probability_passing_qc = round(float(np.clip(100 - (combined_risk ** 1.3) * 100, 1, 99.5)), 1)

    if combined_risk >= 0.66:
        risk_level = "high"
    elif combined_risk >= 0.33:
        risk_level = "medium"
    else:
        risk_level = "low"

    confidence = round(float(np.clip(0.5 + 0.1 * len(factors), 0.5, 0.96)), 2)
    sampling = recommend_sampling_rate(quality_score, risk_level, confidence)

    rule_based_summary = (
        f"Quality score {quality_score}/100 ({risk_level} risk) - estimated {probability_passing_qc}% probability "
        f"of passing QC. {'; '.join(factors[:2]) if factors else 'No elevated risk factors identified.'}"
    )

    return {
        "work_order_id": work_order.work_order_id,
        "batch_id": work_order.batch_id,
        "machine_id": work_order.machine_id,
        "quality_score": quality_score,
        "risk_level": risk_level,
        "probability_passing_qc": probability_passing_qc,
        "confidence": confidence,
        "contributing_factors": factors,
        "sampling_rate_pct": sampling["sampling_rate_pct"],
        "sampling_rationale": sampling["sampling_rationale"],
        "ai_summary": rule_based_summary,  # placeholder until/unless enrich_with_llm_summary upgrades it
        "llm_generated": False,
    }


def enrich_with_llm_summary(result: dict) -> dict:
    """
    Pure function, no DB access - safe to call from a thread pool. Takes
    a score_batch_deterministic() result and tries to upgrade its
    ai_summary with Claude's phrasing of the same already-computed facts.
    Leaves the result untouched if the LLM is unavailable/fails.
    """
    llm_text = _llm_summary(
        result["work_order_id"], result["quality_score"], result["risk_level"],
        result["probability_passing_qc"], result["confidence"], result["contributing_factors"],
    )
    if llm_text:
        result["ai_summary"] = llm_text
        result["llm_generated"] = True
    return result


def score_batch(db: Session, work_order: WorkOrder) -> dict:
    """Convenience wrapper for scoring a single batch end-to-end
    (deterministic + LLM enrichment) - used by call sites that only
    need one batch and don't need to parallelize anything."""
    result = score_batch_deterministic(db, work_order)
    return enrich_with_llm_summary(result)
