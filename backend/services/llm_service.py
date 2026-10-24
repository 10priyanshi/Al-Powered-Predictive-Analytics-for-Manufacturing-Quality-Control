"""
LLM service
============
The single place every Claude API call in the app goes through.

Design principle (non-negotiable): the LLM is used ONLY to phrase or
synthesize prose from facts this service is handed - it never computes a
risk score, a category split, or a confidence value itself. Every prompt
built here explicitly instructs Claude to use only the supplied facts and
never introduce a number, cause, or claim that isn't in them. That keeps
the app's explainability/auditability guarantee intact even though the
wording now comes from an LLM instead of a template.

Failure handling: if ANTHROPIC_API_KEY isn't set, or the API call fails
for any reason (no network, invalid key, rate limit, timeout), every
function here returns None. Callers are required to fall back to their
original rule-based/templated text in that case - see rca_agent.py,
recommendation_agent.py, and chatbot_agent.py for the fallback pattern.
This means the app works identically end-to-end with or without a key;
Claude only ever makes the output read better, never gates functionality.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger("qualityguard.llm")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("QUALITYGUARD_LLM_MODEL", "claude-sonnet-5")
TIMEOUT_SECONDS = 12.0

_client = None
_client_init_failed = False


def is_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _get_client():
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed or not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=TIMEOUT_SECONDS)
        return _client
    except Exception as e:
        logger.warning(f"Could not initialize Anthropic client - LLM features will fall back to rule-based text: {e}")
        _client_init_failed = True
        return None


def generate(system_prompt: str, user_prompt: str, max_tokens: int = 400) -> Optional[str]:
    """
    Returns Claude's response text, or None if the LLM is unavailable or
    the call fails for any reason. Callers MUST handle the None case by
    falling back to their existing rule-based text - this is never the
    only path to a working response.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if response.content and len(response.content) > 0:
            text = response.content[0].text.strip()
            return text if text else None
        return None
    except Exception as e:
        logger.warning(f"LLM call failed, falling back to rule-based text: {e}")
        return None


# ---------------------------------------------------------------------------
# Shared system prompt fragment reused across all three call sites, so the
# "only use the given facts" guardrail is worded identically everywhere.
# ---------------------------------------------------------------------------
GROUNDING_RULE = (
    "You are writing for a manufacturing quality-control system. You will be given a set "
    "of FACTS that were computed by deterministic rules/models - not by you. Your only job "
    "is to phrase those facts as clear, natural prose a quality engineer would write. "
    "Strict rules: never invent a number, cause, machine, defect, or outcome that is not "
    "explicitly present in the FACTS given to you. Never change or round any number that "
    "appears in the FACTS. If the FACTS are sparse, write a shorter answer rather than "
    "padding it with invented detail. Do not add hedges like 'as an AI' or meta-commentary "
    "about your own reasoning process - just write the answer directly."
)
