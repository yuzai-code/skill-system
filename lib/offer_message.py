"""OfferMessage — the prompt fragment injected when OfferGate fires.

CLI-agnostic. Adapters wrap this in their native injection mechanism
(Stop hook stdout / tui.prompt.append / hooks.json custom command stdout).
The message is imperative, carries a quantified reason, and instructs the
agent to ASK THE USER FIRST — never silently call skill_manage(create).

Why imperative + quantified: the original 3-line system prompt said
"after complex tasks, save the approach" — passive, no signal. This
message arrives exactly when the gate fired, so it can state the reason
("12 tool calls, 1 recovery") which gives the agent concrete grounds to
act rather than a vague reminder.
"""

from __future__ import annotations

from typing import Iterable

from .offer_gate import GateResult
from .session_profile import SessionProfile

TEMPLATE = """\
[skill-system] Your last session met the complexity threshold for skill capture
(reason: {reason}; profile: {tool_calls} tool calls, {recoveries} recoveries, {corrections} corrections, {distinct} distinct tools).

This looks like a reusable workflow. Follow this exactly:
1. Summarize the just-completed approach in 2-3 sentences to the user.
2. Ask the user whether they'd like it saved as a skill. Wait for a yes/no.
3. Only on explicit "yes", call skill_manage(action="create", name="<kebab-slug>", content="<full SKILL.md>") with the 8-section format.
   - Do NOT create a skill if the user declines or is unsure.
   - Do NOT create a skill for trivial one-off tasks even if this message appears.

Hard constraints still apply: description <= 60 chars, author = "skill-system".
To silence these offers: run `skill-offer --pause`.
"""


def render(p: SessionProfile, result: GateResult) -> str:
    """Render the injection fragment from a profile + gate result.

    Only call this when result.should_offer is True.
    """
    return TEMPLATE.format(
        reason=result.reason,
        tool_calls=p.tool_calls,
        recoveries=p.error_recoveries,
        corrections=p.user_corrections,
        distinct=len(p.distinct_tools),
    )


def render_declined_ack() -> str:
    """One-line ack injected (optionally) when the user declines.

    Keeps the agent from looping on the offer. Adapters may omit this
    entirely and just clear cooldown state.
    """
    return "[skill-system] Offer declined. I won't suggest saving this session again."


def render_for_adapter(p: SessionProfile, result: GateResult) -> dict:
    """Structured form for adapters that inject via a structured channel
    (e.g. OpenCode tui.prompt.append accepts a string, but some MCP-ish
    channels take an object). Returns {message, reason, rules_fired}.
    """
    return {
        "message": render(p, result) if result.should_offer else "",
        "reason": result.reason,
        "rules_fired": list(result.rules_fired),
        "score": result.score,
        "blocked_by_cooldown": result.blocked_by_cooldown,
    }


def summarize_rules(rules: Iterable[str]) -> str:
    """Human-readable one-liner of which rules fired. Used in logs/CLI."""
    names = {
        "tool_calls": "many tool calls",
        "error_recoveries": "error recovery",
        "user_corrections": "user correction",
        "distinct_tools": "multi-tool combination",
    }
    return ", ".join(names.get(r, r) for r in rules) or "none"
