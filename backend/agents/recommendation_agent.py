"""
Recommendation (RAC) Agent
===========================
Turns an RCA result into a specific, groundable corrective-action
recommendation. Recommendations are NOT generated as free-form text -
they must cite at least the specific machine/material/process fact
that triggered them, and are checked against a small in-app knowledge
base of resolved past cases so the action has precedent, not just a
plausible-sounding guess.

Every recommendation returned includes: the action, the reasoning, a
confidence score, and the similar-case IDs used as precedent - the
human Approve/Reject/Modify step happens in the API layer, never here.
"""
from typing import Optional
import re
import numpy as np
from sqlalchemy.orm import Session

from backend.models.defect import Defect
from backend.models.machine import Machine
from backend.models.mes import WorkOrder, MaterialLot
from backend.models.knowledge_base import SimilarPastCase
from backend.services import llm_service


def _find_similar_cases(db: Session, defect_type: str, category: str, limit: int = 2) -> list[SimilarPastCase]:
    exact = (
        db.query(SimilarPastCase)
        .filter(SimilarPastCase.defect_type == defect_type)
        .limit(limit)
        .all()
    )
    if len(exact) >= limit:
        return exact
    by_category = (
        db.query(SimilarPastCase)
        .filter(SimilarPastCase.category == category, SimilarPastCase.defect_type != defect_type)
        .limit(limit - len(exact))
        .all()
    )
    return exact + by_category


def _action_for_category(category: str, defect: Defect, machine: Optional[Machine], lot: Optional[MaterialLot]) -> str:
    if category == "equipment":
        if machine and machine.next_maintenance_due:
            return (
                f"Schedule maintenance/inspection on {defect.machine_id} - flagged as overdue or trending "
                f"toward its service window. Prioritize the component associated with '{defect.defect_type.value}' defects."
            )
        return f"Schedule an equipment inspection on {defect.machine_id} focused on the {defect.location} area."
    if category == "material":
        if lot:
            return (
                f"Quarantine remaining stock from material lot {lot.lot_id} ({lot.supplier_name}) and request "
                f"updated material certification before further use."
            )
        return f"Review the material lot used for batch {defect.batch_id} for quality certification compliance."
    if category == "process":
        return (
            f"Review process parameters at {defect.location} against the documented spec for this product/SKU; "
            f"verify no recent changeover or setting drift caused the deviation."
        )
    # human
    return (
        f"Review the work instruction and handling procedure at {defect.location}; consider retraining or adding "
        f"a fixture/checklist step if this recurs after shift handoffs or changeovers."
    )


def _llm_recommendation(defect, category, similar_cases, contributing_factors, rule_based_action, rule_based_reasoning):
    """
    Asks Claude to rewrite the already-computed action + reasoning as
    natural prose for the assigned reviewer. The action itself (what to
    physically do) and which precedent case(s) back it were already
    decided by _action_for_category()/_find_similar_cases() above -
    Claude's job is only to phrase them clearly and is explicitly told
    to keep any precedent case ID verbatim. Returns None on failure so
    the caller falls back to the rule-based text unchanged.
    """
    precedent_text = (
        "\n".join(
            f"- {c.case_id}: {c.description} Root cause: {c.root_cause} Action taken: {c.action_taken} Outcome: {c.outcome}"
            for c in similar_cases
        ) if similar_cases else "No matching precedent case in the knowledge base."
    )
    facts = (
        f"Defect type: {defect.defect_type.value}\n"
        f"Machine: {defect.machine_id}\n"
        f"Location: {defect.location}\n"
        f"Root-cause category (already determined): {category}\n"
        f"Contributing evidence:\n" + "\n".join(f"- {f}" for f in contributing_factors[:4]) + "\n\n"
        f"Rule-computed recommended action (base this on it, do not contradict it): {rule_based_action}\n\n"
        f"Similar past cases available as precedent:\n{precedent_text}"
    )
    user_prompt = (
        f"{facts}\n\n"
        f"Write two things for the reviewer of this recommendation:\n"
        f"1) ACTION: one clear, specific sentence stating the corrective action to take (based on the rule-computed "
        f"action above - do not change what it recommends doing, just phrase it naturally). If a precedent case ID "
        f"like CASE-2000 is listed above, mention it by that exact ID.\n"
        f"2) REASONING: 1-2 sentences explaining why, referencing the specific evidence above.\n"
        f"Respond in exactly this format with no other text:\nACTION: <text>\nREASONING: <text>"
    )
    raw = llm_service.generate(llm_service.GROUNDING_RULE, user_prompt, max_tokens=260)
    if not raw:
        return None
    action_match = re.search(r"ACTION:\s*(.+?)(?=\nREASONING:|$)", raw, re.DOTALL)
    reasoning_match = re.search(r"REASONING:\s*(.+)", raw, re.DOTALL)
    if not action_match or not reasoning_match:
        return None
    action = action_match.group(1).strip()
    reasoning = reasoning_match.group(1).strip()
    if not action or not reasoning:
        return None
    return {"action_text": action, "reasoning": reasoning}


def generate_recommendation(db: Session, defect: Defect, rca_result: dict) -> dict:
    category = rca_result["primary_category"]
    machine = db.query(Machine).filter(Machine.machine_id == defect.machine_id).first()
    work_order = db.query(WorkOrder).filter(WorkOrder.batch_id == defect.batch_id).first() if defect.batch_id else None
    lot = db.query(MaterialLot).filter(MaterialLot.lot_id == work_order.material_lot_id).first() if work_order else None

    similar_cases = _find_similar_cases(db, defect.defect_type.value, category)
    action_text = _action_for_category(category, defect, machine, lot)

    if similar_cases:
        precedent = similar_cases[0]
        action_text += f" (Precedent: {precedent.case_id} - {precedent.action_taken})"

    case_match_strength = 0.15 * len(similar_cases)
    confidence = round(float(np.clip(rca_result["confidence"] * 0.7 + case_match_strength, 0.35, 0.97)), 2)

    reasoning = (
        f"Root-cause analysis assigned this defect primarily to '{category}' "
        f"({rca_result['category_split'][category]:.0%} confidence-weighted); "
        f"{'; '.join(rca_result['contributing_factors'][:3])}. "
        f"{'Grounded against ' + str(len(similar_cases)) + ' similar past case(s) in the knowledge base.' if similar_cases else 'No closely matching past case was found in the knowledge base.'}"
    )

    llm_generated = False
    llm_result = _llm_recommendation(defect, category, similar_cases, rca_result["contributing_factors"], action_text, reasoning)
    if llm_result:
        action_text, reasoning = llm_result["action_text"], llm_result["reasoning"]
        llm_generated = True

    return {
        "action_text": action_text,
        "reasoning": reasoning,
        "confidence": confidence,
        "similar_case_ids": [c.case_id for c in similar_cases],
        "assigned_role": rca_result["routed_role"],
        "llm_generated": llm_generated,
    }


def generate_preprod_recommendation(risk_factors: list[str], risk_score: float) -> str:
    """Chooses a pre-emptive action for a scheduled batch based on which
    signal dominates the contributing reasons, per the spec's action set:
    delay batch, swap machine, increase inspection sampling, request certs."""
    joined = " ".join(risk_factors).lower()
    if "material lot" in joined or "contamination" in joined or "supplier" in joined:
        return "Request updated material certification for the assigned lot before releasing this batch to production."
    if "maintenance" in joined or "overdue" in joined or "bearing" in joined or "vibration" in joined:
        return "Swap to an alternate machine if available, or delay the batch until scheduled maintenance is completed."
    if "defect history" in joined or "sku" in joined:
        return "Increase inspection sampling rate for this batch given the SKU's elevated historical defect rate."
    if risk_score >= 70:
        return "Delay this batch until the contributing risk factors are addressed."
    return "Increase inspection sampling rate as a precaution; no single dominant risk factor identified."
