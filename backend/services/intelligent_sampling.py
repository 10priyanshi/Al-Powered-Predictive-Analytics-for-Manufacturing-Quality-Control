"""
Intelligent Sampling
======================
Rather than inspecting every batch at the same rate, recommends an
inspection intensity (% of units to sample) driven directly by the
Batch Quality Score's risk_level and confidence - reusing that already-
computed, explained score rather than re-deriving a separate judgment.

Kept as a plain disclosed rule table (not an LLM call) on purpose: the
thresholds here are a compliance-relevant decision (how much of a batch
gets inspected), so it stays fully deterministic and auditable rather
than routed through a model that could phrase it differently between
runs. This mirrors the same "explainable over black-box" principle used
throughout the rest of the app.
"""

# Disclosed thresholds - QA can review and tune these directly, unlike
# a model's internal weights.
HIGH_RISK_SAMPLING_PCT = 100.0
MEDIUM_RISK_SAMPLING_PCT = 20.0
LOW_RISK_LOW_CONFIDENCE_SAMPLING_PCT = 20.0
LOW_RISK_HIGH_CONFIDENCE_SAMPLING_PCT = 5.0
LOW_CONFIDENCE_THRESHOLD = 0.65


def recommend_sampling_rate(quality_score: float, risk_level: str, confidence: float) -> dict:
    if risk_level == "high" or quality_score < 50:
        rate = HIGH_RISK_SAMPLING_PCT
        rationale = (
            f"Risk level is '{risk_level}' (quality score {quality_score}/100) - full inspection recommended "
            f"before this batch is released."
        )
    elif risk_level == "medium":
        rate = MEDIUM_RISK_SAMPLING_PCT
        rationale = (
            f"Risk level is 'medium' (quality score {quality_score}/100) - partial sampling recommended to "
            f"verify quality without a full teardown."
        )
    else:  # low risk
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            rate = LOW_RISK_LOW_CONFIDENCE_SAMPLING_PCT
            rationale = (
                f"Risk level is 'low', but confidence is only {confidence:.0%} due to limited supporting data - "
                f"a spot-check sample is recommended rather than skipping inspection entirely."
            )
        else:
            rate = LOW_RISK_HIGH_CONFIDENCE_SAMPLING_PCT
            rationale = (
                f"Risk level is 'low' with {confidence:.0%} confidence - minimal sampling is sufficient."
            )
    return {"sampling_rate_pct": rate, "sampling_rationale": rationale}
