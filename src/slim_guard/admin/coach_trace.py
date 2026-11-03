"""Read-only presentation for direct coach traces, independent of legacy rewrite traces."""

from typing import Any


def direct_style_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    finals = [a for a in artifacts if a.get("artifact_type") == "outgoing_messages"]
    if not finals:
        return None
    final = finals[-1]
    payload = final.get("payload") or {}
    failed = bool(payload.get("failure_code"))
    return {
        "style_profile_version": payload.get("style_profile_version"),
        "status": "degraded" if failed else "adopted",
        "bypassed": False,
        "bypass_reason": None,
        "degraded": failed,
        "degraded_reason": payload.get("failure_code"),
        "adopted": not failed,
        "adopted_artifact_id": final.get("artifact_id"),
        "adoption_mode": "coach_direct",
        "final": True,
    }
