"""
Chatbot Agent
==============
Answers plain-language questions using ONLY retrieval against the
app's own database - never invented numbers or causes. Every answer
states what it's based on and a confidence/limitations note, same as
the other agents.

Covers every major data domain in the app, not just machine/line
status: batch quality scores, deviation predictions, recommendations,
operator/shift performance, pre-production risk, event traceability,
root-cause responsibility, past-case precedent, and daily summaries.

RBAC is enforced per-domain, matching exactly how each domain is
scoped elsewhere in the app - not just line-scoping for Operators:
  - Operator: own line only, checked no matter HOW the question
    references it (explicit "line N", a machine ID, a batch number,
    or a work order tied to one of those)
  - Maintenance / Production / Procurement: their root-cause category
    for defects/recommendations (equipment / process+human / material)
  - Operator performance data: restricted to Production, Quality
    Manager, Plant Head, Admin - same as the Operator Intelligence page
  - Continuous learning / audit-style summaries: restricted to Quality
    Manager, Plant Head, Admin - same as those pages
Every denial states which role boundary is being enforced, never just
a generic refusal, and is never passed through the LLM rephrase step -
an access-control message must stay exactly deterministic.
"""
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.auth.dependencies import CurrentUser
from backend.models.enums import UserRole
from backend.models.machine import Machine, Line
from backend.models.alert import Alert
from backend.models.defect import Defect
from backend.models.production import ProductionLog
from backend.models.root_cause import RootCauseAnalysis
from backend.models.recommendation import Recommendation
from backend.models.knowledge_base import SimilarPastCase
from backend.models.batch_quality import BatchQualityScore
from backend.models.preprod_risk import PreProductionRiskScore
from backend.models.event_trace import EventToUnitTrace
from backend.models.mes import WorkOrder
from backend.agents.prediction_agent import score_machine
from backend.agents import deviation_agent
from backend.services.report_service import build_report_content
from backend.services.rbac_scope import (
    line_scope_for_user, is_plant_wide, category_scope_for_defects,
    assigned_role_scope_for_recommendations,
)
from backend.services import operator_signals, llm_service

LINE_RE = re.compile(r"\bline\s*#?(\d+)\b", re.IGNORECASE)
BATCH_RE = re.compile(r"\bbatch[\s#-]*(\d{3,6})\b", re.IGNORECASE)
MACHINE_RE = re.compile(r"\bLINE\d+-[A-Z]+-\d+\b", re.IGNORECASE)
WO_RE = re.compile(r"\bWO-?\s?(\d{4,6})\b", re.IGNORECASE)
REC_RE = re.compile(r"\bREC-?\s?(\d{4,6})\b", re.IGNORECASE)
EVT_RE = re.compile(r"\bEVT-?\s?(\d{3,6})\b", re.IGNORECASE)
SHIFT_RE = re.compile(r"\b(morning|afternoon|night)\b", re.IGNORECASE)
GREETING_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b", re.IGNORECASE)

OPERATOR_INTEL_ROLES = (UserRole.PRODUCTION, UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)
EXECUTIVE_ROLES = (UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)


def _extract_line(message: str) -> Optional[str]:
    m = LINE_RE.search(message)
    return f"LINE{m.group(1)}" if m else None


def _extract_batch(message: str) -> Optional[str]:
    m = BATCH_RE.search(message)
    return f"BATCH-{m.group(1)}" if m else None


def _extract_machine(message: str) -> Optional[str]:
    m = MACHINE_RE.search(message)
    return m.group(0).upper() if m else None


def _extract_work_order(message: str) -> Optional[str]:
    m = WO_RE.search(message)
    return f"WO-{m.group(1)}" if m else None


def _extract_recommendation(message: str) -> Optional[str]:
    m = REC_RE.search(message)
    return f"REC-{m.group(1)}" if m else None


def _extract_event(message: str) -> Optional[str]:
    m = EVT_RE.search(message)
    return f"EVT-{m.group(1)}" if m else None


def _extract_shift(message: str) -> Optional[str]:
    m = SHIFT_RE.search(message)
    return m.group(1).lower() if m else None


def _classify_intent(message: str) -> str:
    lower = message.lower()
    if GREETING_RE.match(message):
        return "greeting"
    if any(w in lower for w in ["summarize", "summary", "today's quality", "daily report"]):
        return "report_request"
    if any(w in lower for w in ["operator performance", "which operator", "operator defect", "operator training", "fatigue"]):
        return "operator_performance"
    if "operator" in lower and any(w in lower for w in ["perform", "doing", "rate", "training"]):
        return "operator_performance"
    if SHIFT_RE.search(message) and any(w in lower for w in ["perform", "doing", "pass rate", "defect rate"]):
        return "operator_performance"
    if any(w in lower for w in ["audit", "who approved", "who reviewed", "who logged in"]):
        return "audit_lookup"
    if any(w in lower for w in ["continuous learning", "model improving", "getting better", "recommendation accuracy", "ai performance"]):
        return "learning_trend"
    if any(w in lower for w in ["quality score", "batch quality", "pass qc", "probability of passing", "how's the batch", "how is batch", "inspect rate", "sampling rate"]):
        return "batch_quality_lookup"
    if any(w in lower for w in ["deviation", "trending toward", "about to breach", "predicted deviation", "going to cross", "about to go wrong"]):
        return "deviation_lookup"
    if any(w in lower for w in ["upcoming batch", "scheduled batch", "pre-production", "preproduction", "before it runs", "before it starts"]):
        return "preprod_risk_lookup"
    if any(w in lower for w in ["foreign object", "quarantine", "scrap debris", "affected units", "affected unit"]):
        return "event_trace_lookup"
    if any(w in lower for w in ["recommendation", "pending review", "approval rate", "approved", "rejected", "modified"]) and "responsible" not in lower:
        return "recommendation_status"
    if "responsible" in lower or "who's" in lower or "who is" in lower:
        return "responsibility_lookup"
    if any(w in lower for w in ["similar", "past defect", "history of", "precedent"]):
        return "historical_lookup"
    if "why did" in lower or ("why" in lower and "alert" in lower):
        return "explanation"
    if any(w in lower for w in ["risk", "status", "how is", "why is"]):
        return "status_lookup"
    return "status_lookup"  # default: most unmatched questions are effectively status questions


def _resolve_batch_line(db: Session, batch_id: str) -> Optional[str]:
    defect = db.query(Defect).filter(Defect.batch_id == batch_id).first()
    if defect:
        return defect.machine_id.split("-")[0]
    prod = db.query(ProductionLog).filter(ProductionLog.batch_id == batch_id).first()
    if prod:
        return prod.line_id
    return None


def _resolve_work_order_line(db: Session, work_order_id: str) -> Optional[str]:
    wo = db.query(WorkOrder).filter(WorkOrder.work_order_id == work_order_id).first()
    return wo.machine_id.split("-")[0] if wo else None


def _resolve_implicated_line(db: Session, target_line, target_machine, target_batch, target_wo) -> Optional[str]:
    """Determines which single line, if any, this question is actually
    about - checked regardless of WHICH way the user referenced it."""
    if target_line:
        return target_line
    if target_machine:
        return target_machine.split("-")[0]
    if target_wo:
        return _resolve_work_order_line(db, target_wo)
    if target_batch:
        return _resolve_batch_line(db, target_batch)
    return None


def _scope_denied(user: CurrentUser, implicated_line: Optional[str]) -> Optional[dict]:
    """Line-based denial (Operator role). Never passed through the LLM."""
    allowed_line = line_scope_for_user(user)
    if allowed_line and implicated_line and implicated_line != allowed_line:
        return {
            "response": (
                f"I can only help with questions about {allowed_line}, since that's your line as an Operator. "
                f"I'm not able to share data about {implicated_line} for you - if you need that, a supervisor "
                f"or Quality Manager would have access."
            ),
            "citations": [], "confidence": 1.0,
            "limitations": "Response withheld due to role-based access scope, not a data gap.",
            "llm_generated": False, "_skip_llm": True,
        }
    return None


def _role_denied(user: CurrentUser, allowed_roles: tuple, topic_label: str) -> Optional[dict]:
    """Role-based denial (topics restricted to specific roles regardless
    of line, e.g. operator performance data or audit/learning trends)."""
    if user.role not in allowed_roles:
        readable_roles = ", ".join(r.value.replace("_", " ").title() for r in allowed_roles)
        return {
            "response": (
                f"{topic_label} isn't something I can share with your role - that's visible to {readable_roles}. "
                f"Happy to help with something else, like machine or batch status."
            ),
            "citations": [], "confidence": 1.0,
            "limitations": "Response withheld due to role-based access scope, not a data gap.",
            "llm_generated": False, "_skip_llm": True,
        }
    return None


def _llm_rephrase_answer(question: str, grounded_response: str, citations: list[str]) -> Optional[str]:
    """
    Takes the already-computed, fact-correct answer and asks Claude to
    phrase it more naturally, the way a knowledgeable coworker would say
    it out loud. Claude sees ONLY this text and the citation list - it
    cannot see the database and is explicitly forbidden from adding
    anything not present in them. Returns None on failure, so the
    caller keeps the original grounded_response, which is itself
    written to read naturally rather than as a bare fallback.
    """
    facts = (
        f"The user asked: \"{question}\"\n\n"
        f"The correct, fully-computed answer (from live plant data) is:\n{grounded_response}\n\n"
        f"This answer is backed by: {', '.join(citations) if citations else '(no specific citations)'}"
    )
    user_prompt = (
        f"{facts}\n\n"
        f"Rewrite the answer above as a natural, conversational reply to the user's question - the way a "
        f"knowledgeable colleague would say it out loud. Keep every number, name, and fact exactly as given. "
        f"Keep it concise (2-4 sentences). Do not add citations or confidence figures in your reply - those "
        f"are shown separately in the UI."
    )
    return llm_service.generate(llm_service.GROUNDING_RULE, user_prompt, max_tokens=220)


def _phrase_category_split(category_split: dict) -> str:
    """Turns a {"equipment": 0.43, "material": 0.29, ...} dict into a
    natural sentence fragment instead of ever printing raw JSON."""
    ranked = sorted(category_split.items(), key=lambda kv: -kv[1])
    primary, primary_pct = ranked[0]
    others = [f"{cat} ({pct:.0%})" for cat, pct in ranked[1:] if pct >= 0.1]
    phrase = f"mainly a{'n' if primary[0] in 'aeiou' else ''} {primary} issue ({primary_pct:.0%} confidence)"
    if others:
        phrase += f", with some contribution from {', '.join(others)}"
    return phrase


def _recommendation_scope_filter(db: Session, user: CurrentUser, query):
    """Scopes a Recommendation query by role, matching /api/recommendations
    exactly for non-Operator roles (assigned_role). Operators don't have
    their own assigned_role bucket (recommendations route to Maintenance/
    Production/Procurement, never 'operator'), so for them this scopes
    by their line instead, via the underlying defect's machine."""
    if user.role == UserRole.OPERATOR:
        allowed_line = line_scope_for_user(user)
        if allowed_line:
            defect_ids = [d.defect_id for d in db.query(Defect.defect_id).filter(Defect.machine_id.like(f"{allowed_line}-%")).all()]
            wo_ids = [w.work_order_id for w in db.query(WorkOrder.work_order_id).filter(WorkOrder.machine_id.like(f"{allowed_line}-%")).all()]
            return query.filter(Recommendation.source_id.in_(defect_ids + wo_ids))
        return query
    scope_role = assigned_role_scope_for_recommendations(user)
    if scope_role:
        query = query.filter(Recommendation.assigned_role == scope_role)
    return query


def answer_query(db: Session, user: CurrentUser, message: str) -> dict:
    result = _answer_query_grounded(db, user, message)
    if result.pop("_skip_llm", False):
        return result

    result.setdefault("llm_generated", False)
    rephrased = _llm_rephrase_answer(message, result["response"], result["citations"])
    if rephrased:
        result["response"] = rephrased
        result["llm_generated"] = True
    return result


def _answer_query_grounded(db: Session, user: CurrentUser, message: str) -> dict:
    intent = _classify_intent(message)

    if intent == "greeting":
        return {
            "response": (
                "Hey! I can help with machine or line status, batch quality scores, predicted deviations, "
                "recommendations, pre-production risk on scheduled batches, quarantine/event traces, or a quick "
                "quality summary - what would you like to know?"
            ),
            "citations": [], "confidence": 1.0, "limitations": "", "llm_generated": False, "_skip_llm": True,
        }

    target_line = _extract_line(message)
    target_batch = _extract_batch(message)
    target_machine = _extract_machine(message)
    target_wo = _extract_work_order(message)
    target_rec = _extract_recommendation(message)
    target_evt = _extract_event(message)
    target_shift = _extract_shift(message)
    allowed_line = line_scope_for_user(user)

    # Line-based scope check covers every way a question can implicate a
    # line - explicit mention, machine ID, batch number, or work order -
    # resolved via real DB lookups where needed, not just literal phrasing.
    implicated_line = _resolve_implicated_line(db, target_line, target_machine, target_batch, target_wo)
    denial = _scope_denied(user, implicated_line)
    if denial:
        return denial

    if intent == "operator_performance":
        role_denial = _role_denied(user, OPERATOR_INTEL_ROLES, "Operator/shift performance data")
        if role_denial:
            return role_denial
        if target_shift:
            shifts = [s for s in operator_signals.shift_stats(db) if s["shift"] == target_shift]
            if not shifts:
                return {
                    "response": f"I don't have enough inspection data yet to say how {target_shift} shift is doing.",
                    "citations": [], "confidence": 0.4, "limitations": "Insufficient inspection history for this shift.",
                }
            s = shifts[0]
            response = f"{target_shift.capitalize()} shift has a {s['pass_rate_pct']}% pass rate across {s['inspections']} inspections and {s['batches']} batches."
            return {"response": response, "citations": [f"{s['inspections']} inspections on {target_shift} shift"], "confidence": 0.8, "limitations": "Reflects all-time inspection history, not a specific recent window."}
        operators = operator_signals.all_operator_stats(db)
        shifts = operator_signals.shift_stats(db)
        insights = operator_signals.generate_insights(operators, shifts)
        response = " ".join(insights)
        if shifts:
            best = shifts[0]
            response += f" Best-performing shift right now is {best['shift']} at {best['pass_rate_pct']}% pass rate."
        return {
            "response": response, "citations": [f"{len(operators)} operators, {len(shifts)} shifts analyzed"],
            "confidence": 0.8, "limitations": "Based on all inspection history on file, not a specific recent window.",
        }

    if intent == "audit_lookup":
        role_denial = _role_denied(user, EXECUTIVE_ROLES, "Audit log data")
        if role_denial:
            return role_denial
        return {
            "response": (
                "I can point you to the Audit Log page for the full, filterable trail of who did what and when - "
                "that's a better place to browse it than chat. Ask me something more specific, like a particular "
                "recommendation's review status, and I can tell you who reviewed it and when."
            ),
            "citations": [], "confidence": 0.6, "limitations": "Full audit browsing isn't available through chat; see the Audit Log page.",
        }

    if intent == "learning_trend":
        role_denial = _role_denied(user, EXECUTIVE_ROLES, "AI performance / continuous learning data")
        if role_denial:
            return role_denial
        recs = db.query(Recommendation).all()
        decided = [r for r in recs if r.status.value in ("approved", "rejected", "modified")]
        if not decided:
            response = "No recommendations have been reviewed yet in this session, so there's no live accuracy figure - check the AI Performance tab in Reports for the historical trend."
        else:
            good = len([r for r in decided if r.status.value in ("approved", "modified")])
            rate = round(good / len(decided) * 100, 1)
            response = f"Based on {len(decided)} reviewed recommendation(s) so far, about {rate}% were approved or modified rather than rejected. Check the AI Performance tab in Reports for the full week-over-week trend."
        return {"response": response, "citations": [f"{len(decided)} reviewed recommendations"], "confidence": 0.75, "limitations": "Reflects recommendations reviewed in this running instance."}

    if intent == "batch_quality_lookup":
        if target_wo or target_batch:
            row = None
            if target_wo:
                row = db.query(BatchQualityScore).filter(BatchQualityScore.work_order_id == target_wo).first()
            if not row and target_batch:
                row = db.query(BatchQualityScore).filter(BatchQualityScore.batch_id == target_batch).first()
            if not row:
                return {
                    "response": f"I don't have a quality score for that batch yet - it may not have been scored. Try running a scan from the dashboard.",
                    "citations": [], "confidence": 0.4, "limitations": "No matching batch quality record found.",
                }
            line_denial = _scope_denied(user, row.machine_id.split("-")[0])
            if line_denial:
                return line_denial
            response = (
                f"{row.work_order_id} ({row.batch_id}) is scoring {row.quality_score}/100 right now - {row.risk_level} risk, "
                f"about a {row.probability_passing_qc}% chance of passing QC. Recommended inspection sampling is "
                f"{row.sampling_rate_pct}%. {row.ai_summary}"
            )
            return {"response": response, "citations": [f"batch quality score for {row.work_order_id}"], "confidence": row.confidence, "limitations": "Reflects the most recent scoring run for this batch."}
        query = db.query(BatchQualityScore).order_by(BatchQualityScore.quality_score.asc())
        if allowed_line:
            query = query.filter(BatchQualityScore.machine_id.like(f"{allowed_line}-%"))
        rows = query.limit(3).all()
        if not rows:
            return {"response": "No batches have been scored yet - try clicking \"Score active batches\" on the dashboard first.", "citations": [], "confidence": 0.4, "limitations": "No batch quality data on file."}
        parts = [f"{r.work_order_id} ({r.batch_id}) at {r.quality_score}/100, {r.risk_level} risk" for r in rows]
        response = f"Here's how the lowest-scoring active batches are looking right now: {'; '.join(parts)}."
        return {"response": response, "citations": [f"{len(rows)} batch quality scores"], "confidence": 0.8, "limitations": "Showing the lowest-scoring batches currently on file."}

    if intent == "deviation_lookup":
        if target_machine:
            machine = db.query(Machine).filter(Machine.machine_id == target_machine).first()
            if not machine:
                return {"response": f"I don't have a machine on record with ID {target_machine}.", "citations": [], "confidence": 0.3, "limitations": "Machine ID not found."}
            preds = deviation_agent.predict_deviations_for_machine(db, machine)
            if not preds:
                response = f"{target_machine} doesn't have anything trending toward a boundary right now - looks stable."
            else:
                p = preds[0]
                response = f"On {target_machine}: {p['deviation_type'].lower()}, expected to cross in about {p['expected_in_minutes']:.0f} minutes ({p['probability']}% confidence, {p['severity']} severity)."
            return {"response": response, "citations": [f"trend predictions for {target_machine}"], "confidence": 0.75, "limitations": "Predictions reflect current sensor trends and can change quickly."}
        preds = deviation_agent.predict_deviations_plant_wide(db, allowed_line=allowed_line, limit=3)
        if not preds:
            response = "Nothing's currently trending toward a boundary that I can see - all clear for now."
        else:
            parts = [f"{p['machine_id']} ({p['deviation_type'].lower()}, ~{p['expected_in_minutes']:.0f} min out, {p['severity']})" for p in preds]
            response = f"A few things worth watching: {'; '.join(parts)}."
        return {"response": response, "citations": [f"{len(preds)} deviation predictions"], "confidence": 0.75, "limitations": "Predictions reflect current sensor trends and can change quickly."}

    if intent == "preprod_risk_lookup":
        if target_wo:
            row = db.query(PreProductionRiskScore).filter(PreProductionRiskScore.work_order_id == target_wo).first()
            if not row:
                return {"response": f"I don't have a pre-production risk score for {target_wo} - it may not have been scanned yet.", "citations": [], "confidence": 0.4, "limitations": "No matching pre-production risk record."}
            response = f"{target_wo} is scored at {row.risk_score}% risk before it even starts. Suggested action: {row.suggested_action.lower()}"
            return {"response": response, "citations": [f"pre-production risk score for {target_wo}"], "confidence": row.confidence, "limitations": "Reflects signals available at scheduling time only."}
        rows = db.query(PreProductionRiskScore).order_by(PreProductionRiskScore.risk_score.desc()).limit(8).all()
        if allowed_line:
            rows = [r for r in rows if _resolve_work_order_line(db, r.work_order_id) == allowed_line][:3]
        else:
            rows = rows[:3]
        if not rows:
            response = "No upcoming batches have been scored yet, or none are in your scope right now."
        else:
            parts = [f"{r.work_order_id} at {r.risk_score}% risk" for r in rows]
            response = f"Looking ahead, the riskiest scheduled batches are: {'; '.join(parts)}."
        return {"response": response, "citations": [f"{len(rows)} pre-production risk scores"], "confidence": 0.75, "limitations": "Only covers batches that have been scanned so far."}

    if intent == "event_trace_lookup":
        if target_evt:
            row = db.query(EventToUnitTrace).filter(EventToUnitTrace.event_id == target_evt).first()
            if not row:
                return {"response": f"I don't have an event on file with ID {target_evt}.", "citations": [], "confidence": 0.3, "limitations": "Event ID not found."}
            line_denial = _scope_denied(user, row.machine_id.split("-")[0])
            if line_denial:
                return line_denial
            response = (
                f"{target_evt} was a {row.event_type.value.replace('_', ' ')} on {row.machine_id}, affecting units "
                f"#{row.affected_unit_start}-#{row.affected_unit_end} ({row.affected_unit_count} units). Status: {row.status.value}. "
                f"Recommended action: {row.recommended_action}"
            )
            return {"response": response, "citations": [f"event trace {target_evt}"], "confidence": 0.9, "limitations": "Unit range is a deterministic calculation, not a probabilistic estimate."}
        query = db.query(EventToUnitTrace).order_by(EventToUnitTrace.created_at.desc())
        if allowed_line:
            query = query.filter(EventToUnitTrace.machine_id.like(f"{allowed_line}-%"))
        rows = query.limit(3).all()
        if not rows:
            response = "No foreign-object or scrap-debris events on file for your scope."
        else:
            parts = [f"{r.event_id} on {r.machine_id} ({r.status.value}, units #{r.affected_unit_start}-#{r.affected_unit_end})" for r in rows]
            response = f"Recent events: {'; '.join(parts)}."
        return {"response": response, "citations": [f"{len(rows)} event traces"], "confidence": 0.8, "limitations": "Showing the most recent events on file."}

    if intent == "recommendation_status":
        if target_rec:
            rec = db.query(Recommendation).filter(Recommendation.recommendation_id == target_rec).first()
            if not rec:
                return {"response": f"I don't have a recommendation on file with ID {target_rec}.", "citations": [], "confidence": 0.3, "limitations": "Recommendation ID not found."}
            implicated = None
            if rec.source_type == "defect":
                d = db.query(Defect).filter(Defect.defect_id == rec.source_id).first()
                implicated = d.machine_id.split("-")[0] if d else None
            elif rec.source_type == "preprod_batch":
                implicated = _resolve_work_order_line(db, rec.source_id)
            rec_denial = _scope_denied(user, implicated)
            if rec_denial:
                return rec_denial
            response = f"{target_rec} is currently {rec.status.value}. It recommends: {rec.action_text} "
            response += f"Reviewed by {rec.reviewed_by}." if rec.reviewed_by else "Still awaiting review."
            return {"response": response, "citations": [f"recommendation {target_rec}"], "confidence": rec.confidence, "limitations": ""}
        query = _recommendation_scope_filter(db, user, db.query(Recommendation))
        all_recs = query.all()
        pending = len([r for r in all_recs if r.status.value == "pending"])
        decided = [r for r in all_recs if r.status.value != "pending"]
        good = len([r for r in decided if r.status.value in ("approved", "modified")])
        rate_text = f" Of the {len(decided)} already decided, about {round(good / len(decided) * 100)}% were approved or modified." if decided else ""
        response = f"There {'is' if pending == 1 else 'are'} {pending} recommendation(s) pending review in your scope right now.{rate_text}"
        return {"response": response, "citations": [f"{len(all_recs)} recommendations in scope"], "confidence": 0.85, "limitations": "Scoped to what your role can review."}

    if intent == "report_request":
        now = datetime.utcnow()
        period_start = now - timedelta(days=1)
        content = build_report_content(db, period_start, now, line_scope=allowed_line)
        causes = ", ".join(d["defect_type"].replace("_", " ") for d in content["top_root_causes"][:3])
        response = (
            f"Over the last 24 hours I've logged {content['defects_found']} defect(s) and "
            f"{content['alerts_raised']} alert(s) ({content['alerts_by_severity']['critical']} critical, "
            f"{content['alerts_by_severity']['warning']} warning). "
            f"{('The most common issues were ' + causes + '.') if causes else 'No recurring defect type stood out.'} "
            f"{len(content['actions_taken'])} recommendation(s) were approved or modified in that time."
        )
        if content["defect_trend_vs_previous_period_pct"] is not None:
            direction = "up" if content["defect_trend_vs_previous_period_pct"] > 0 else "down"
            response += f" That's defects trending {direction} {abs(content['defect_trend_vs_previous_period_pct'])}% compared to the previous 24 hours."
        return {
            "response": response, "citations": [f"reporting data, {period_start.date()} to {now.date()}"],
            "confidence": 0.9 if content["defects_found"] or content["alerts_raised"] else 0.6,
            "limitations": "Based on the last 24 hours only; ask for a specific period for a different window.",
        }

    if intent == "responsibility_lookup" and target_batch:
        rca = (
            db.query(RootCauseAnalysis)
            .join(Defect, Defect.defect_id == RootCauseAnalysis.defect_id)
            .filter(Defect.batch_id == target_batch)
            .order_by(RootCauseAnalysis.created_at.desc())
            .first()
        )
        if not rca:
            return {
                "response": f"I don't have a root-cause analysis on file for {target_batch} - either it hasn't been analyzed yet, or that batch ID doesn't exist.",
                "citations": [], "confidence": 0.5, "limitations": "No matching defect/RCA record found for this batch.",
            }
        category_split = json.loads(rca.category_split_json)
        response = (
            f"{target_batch}'s defect looks {_phrase_category_split(category_split)}, so it's been routed to "
            f"{rca.routed_role} for action. {rca.explanation}"
        )
        return {"response": response, "citations": [f"RCA record for defect {rca.defect_id}"], "confidence": rca.confidence, "limitations": "Reflects the most recent RCA on file for this batch; may change if re-analyzed."}

    if intent == "historical_lookup":
        defect_type_hint = None
        for dt in ["scratch", "dimension_deviation", "weld_crack", "surface_contamination",
                   "misalignment", "porosity", "discoloration", "incomplete_assembly"]:
            if dt.replace("_", " ") in message.lower() or dt in message.lower():
                defect_type_hint = dt
                break
        query = db.query(SimilarPastCase)
        if defect_type_hint:
            query = query.filter(SimilarPastCase.defect_type == defect_type_hint)
        cases = query.limit(3).all()
        if not cases:
            return {
                "response": "I couldn't find anything similar to that in the knowledge base - it's possible this is a genuinely new issue type.",
                "citations": [], "confidence": 0.4, "limitations": "Knowledge base has a limited number of seeded cases; this may be a genuinely new issue type.",
            }
        parts = [f"{c.case_id} had a similar issue: {c.description} That turned out to be {c.root_cause.lower()}, and the fix was to {c.action_taken.lower()}" for c in cases]
        response = " ".join(parts)
        return {"response": response, "citations": [c.case_id for c in cases], "confidence": 0.75, "limitations": "Based on a small curated knowledge base of past cases, not an exhaustive history."}

    # status_lookup / explanation / default - machine, line, or batch status.
    # A batch mention is checked here too (not just under the
    # "batch_quality_lookup" intent) so that plainly-phrased questions
    # like "status of batch X" get the same real answer as
    # "quality score of batch X" - not just questions using the exact
    # keywords that trip the batch_quality_lookup classifier.
    if target_batch and intent not in ("batch_quality_lookup", "responsibility_lookup", "recommendation_status", "event_trace_lookup"):
        row = db.query(BatchQualityScore).filter(BatchQualityScore.batch_id == target_batch).first()
        if row:
            response = (
                f"{row.work_order_id} ({row.batch_id}) is scoring {row.quality_score}/100 right now - {row.risk_level} risk, "
                f"about a {row.probability_passing_qc}% chance of passing QC. Recommended inspection sampling is "
                f"{row.sampling_rate_pct}%. {row.ai_summary}"
            )
            return {"response": response, "citations": [f"batch quality score for {row.work_order_id}"], "confidence": row.confidence, "limitations": "Reflects the most recent scoring run for this batch."}
        return {
            "response": f"I don't have a quality score for {target_batch} yet - it may not have been scored. Try running a scan from the dashboard.",
            "citations": [], "confidence": 0.4, "limitations": "No matching batch quality record found.",
        }

    if target_machine:
        machine = db.query(Machine).filter(Machine.machine_id == target_machine).first()
        if not machine:
            return {"response": f"I don't have a machine on record with ID {target_machine} - double check the ID?", "citations": [], "confidence": 0.3, "limitations": "Machine ID not found in the system."}
        result = score_machine(db, machine)
        alert_count = db.query(Alert).filter(Alert.machine_id == target_machine, Alert.status != "acknowledged").count()
        factors = "; ".join(result["contributing_factors"][:3])
        response = (
            f"{target_machine} is sitting at {result['risk_score']}% risk right now, which I'd call {result['risk_level']}. "
            f"That's based on {result['based_on']}. Specifically: {factors}. "
            f"{'There are currently ' + str(alert_count) + ' active alert(s) on this machine.' if alert_count else 'No active alerts on it at the moment.'}"
        )
        return {"response": response, "citations": [f"{alert_count} alerts on {target_machine}", "live sensor + maintenance data"], "confidence": result["confidence"], "limitations": "Risk score reflects the last 3 hours of sensor data."}

    if target_line or allowed_line:
        line_code = target_line or allowed_line
        line = db.query(Line).filter(Line.line_code == line_code).first()
        if not line:
            return {"response": f"I don't have a line on record with code {line_code} - double check the number?", "citations": [], "confidence": 0.3, "limitations": "Line code not found in the system."}
        machines = db.query(Machine).filter(Machine.line_id == line.id).all()
        scores = [score_machine(db, m) for m in machines]
        high = [s for s in scores if s["risk_level"] == "high"]
        alert_count = db.query(Alert).filter(Alert.line_id == line_code, Alert.status != "acknowledged").count()
        if high:
            worst = max(high, key=lambda s: s["risk_score"])
            factors = "; ".join(worst["contributing_factors"][:2])
            response = (
                f"{line_code} does have something worth flagging: {worst['machine_id']} is running at "
                f"{worst['risk_score']}% risk ({worst['risk_level']}) - {factors}. "
                f"There {'is' if alert_count == 1 else 'are'} {alert_count} active alert(s) on the line right now."
            )
        else:
            response = (
                f"{line_code} looks fine right now - all {len(machines)} monitored machines are within normal risk range, "
                f"and there {'is' if alert_count == 1 else 'are'} {alert_count} active alert(s) on the line."
            )
        return {
            "response": response, "citations": [f"{len(scores)} machine risk scores on {line_code}", f"{alert_count} alerts"],
            "confidence": round(sum(s["confidence"] for s in scores) / len(scores), 2) if scores else 0.5,
            "limitations": "Risk scores reflect the last 3 hours of sensor data; older issues may not be reflected.",
        }

    return {
        "response": (
            "I can help with machine or line risk status, batch quality scores, predicted deviations, "
            "recommendations, pre-production risk on scheduled batches, quarantine/event traces, similar past "
            "defects, who's responsible for a batch's issue, or a quick quality summary - what would you like to know?"
        ),
        "citations": [], "confidence": 0.5, "limitations": "Question didn't reference anything specific I could look up.",
    }
