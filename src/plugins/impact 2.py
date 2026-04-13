"""Impact scoring plugin — prioritize followups by expected impact, not only by date."""

from __future__ import annotations

import json

from db import get_followup, score_followup


def handle_impact_score(followup_id: str) -> str:
    """Compute and persist Impact Scoring v1 for one followup."""
    row = get_followup(followup_id)
    if not row:
        return f"ERROR: Followup {followup_id} not found."
    scored = score_followup(followup_id)
    if "error" in scored:
        return f"ERROR: {scored['error']}"
    payload = {
        "followup_id": followup_id,
        "impact_score": scored.get("impact_score", 0),
        "factors": scored.get("impact_factors", {}),
        "reasoning": scored.get("impact_reasoning", ""),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


TOOLS = [
    (handle_impact_score, "nexo_impact_score", "Compute and persist Impact Scoring v1 for a followup so queues can prioritize by expected impact."),
]
