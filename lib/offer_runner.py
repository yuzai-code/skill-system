"""offer_runner — CLI logic shared by bin/skill-profile and bin/skill-offer.

Two CLIs share offer state and the gate. This module wraps the lib
functions into a CLI surface so the bin scripts stay as thin wrappers
(matching the skill-curator pattern).

  skill-profile          # session-end: read profile JSON, evaluate, emit
  skill-offer --status   # user-facing: show / pause / resume / decline
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

SYSTEM_ROOT = Path(__file__).resolve().parent.parent
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

from lib import offer_gate, offer_message  # noqa: E402
from lib.session_profile import SessionProfile, parse_profile, ValidationError  # noqa: E402


def _read_profile_stdin() -> str:
    return sys.stdin.read()


def _emit(p: SessionProfile, result: offer_gate.GateResult, json_out: bool) -> int:
    """Print the offer message (stdout, consumed by the adapter's injector)
    or a structured JSON object. Returns exit code 0 always — a non-offer
    is not an error."""
    if result.should_offer:
        offer_gate.record_offer(p.agent_tool, p)
    if json_out:
        print(json.dumps({
            "should_offer": result.should_offer,
            "reason": result.reason,
            "rules_fired": result.rules_fired,
            "score": result.score,
            "blocked_by_cooldown": result.blocked_by_cooldown,
            "message": offer_message.render(p, result) if result.should_offer else "",
        }, indent=2, ensure_ascii=False))
        return 0
    if result.should_offer:
        msg = offer_message.render(p, result)
        # stdout: the injection fragment (adapters capture this)
        print(msg)
        # stderr: human note (not injected)
        print(
            f"[skill-system] offer emitted for {p.agent_tool} "
            f"({', '.join(result.rules_fired) or 'threshold'})",
            file=sys.stderr,
        )
    else:
        print(f"[skill-system] no offer: {result.reason}", file=sys.stderr)
    return 0


def cmd_profile(argv: list[str]) -> int:
    """skill-profile: evaluate a SessionProfile and emit offer if warranted.

    Input sources (pick one):
      --from-stdin          read JSON from stdin (default if no source)
      --from-file PATH      read JSON from file
      --from-json '...'     inline JSON arg
      --from-log PATH        parse a Codex on_tool_call log into a profile
      --record-create       mark a successful create for --agent-tool (cooldown)
                            (no profile input needed; used by create-hook)

    Output:
      default: offer message to stdout (if should_offer), notes to stderr
      --json: structured JSON to stdout
    """
    from_source = None
    from_file = None
    from_json = None
    record_create = False
    agent_tool_override: Optional[str] = None
    json_out = False
    from_log = None

    it = iter(argv[1:])
    for tok in it:
        if tok == "--from-stdin":
            from_source = "stdin"
        elif tok == "--from-file":
            from_source = "file"
            from_file = next(it, "")
        elif tok == "--from-json":
            from_source = "json"
            from_json = next(it, "")
        elif tok == "--from-log":
            from_source = "log"
            from_log = next(it, "")
        elif tok == "--record-create":
            record_create = True
        elif tok == "--agent-tool":
            agent_tool_override = next(it, "")
        elif tok == "--json":
            json_out = True
        elif tok in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"skill-profile: unknown arg {tok!r}", file=sys.stderr)
            return 2

    if record_create:
        tool = agent_tool_override or "opencode"
        offer_gate.record_create(tool)
        print(f"[skill-system] recorded create for {tool} (cooldown started)")
        return 0

    # read profile JSON
    if from_source == "file":
        if not from_file:
            print("error: --from-file requires a path", file=sys.stderr)
            return 2
        try:
            raw = Path(from_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: cannot read {from_file}: {e}", file=sys.stderr)
            return 1
    elif from_source == "log":
        # Codex adapter: parse an on_tool_call log into a profile.
        if not from_log:
            print("error: --from-log requires a path", file=sys.stderr)
            return 2
        from lib.adapters.codex import parse_log  # local import; only Codex needs it
        try:
            p = parse_log(Path(from_log), agent_tool=agent_tool_override or "codex")
        except OSError as e:
            print(f"error: cannot read log {from_log}: {e}", file=sys.stderr)
            return 1
        result = offer_gate.evaluate(p)
        return _emit(p, result, json_out)
    elif from_source == "json":
        raw = from_json or ""
    else:
        raw = _read_profile_stdin()

    if not raw.strip():
        print("error: empty profile input", file=sys.stderr)
        return 1

    try:
        p = parse_profile(raw)
    except ValidationError as e:
        print(f"error: invalid profile: {e}", file=sys.stderr)
        return 1

    if agent_tool_override:
        p.agent_tool = agent_tool_override

    result = offer_gate.evaluate(p)
    return _emit(p, result, json_out)


def cmd_offer(argv: list[str]) -> int:
    """skill-offer: user-facing offer state management.

    Modes:
      --status             show current state per agent_tool
      --pause              stop emitting offers globally
      --resume             re-enable offers
      --decline            mark the latest offer declined (WAITING->COOLDOWN)
      --reset [tool]       clear state for one tool, or all if omitted
      --agent-tool X       target a specific tool (for --decline/--reset)
    """
    mode: Optional[str] = None
    agent_tool: Optional[str] = None

    it = iter(argv[1:])
    for tok in it:
        if tok in ("--status", "-s"):
            mode = "status"
        elif tok == "--pause":
            mode = "pause"
        elif tok == "--resume":
            mode = "resume"
        elif tok == "--decline":
            mode = "decline"
        elif tok == "--reset":
            mode = "reset"
        elif tok == "--agent-tool":
            agent_tool = next(it, "")
        elif tok in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"skill-offer: unknown arg {tok!r}", file=sys.stderr)
            return 2

    if mode is None:
        print(__doc__)
        return 1

    if mode == "pause":
        offer_gate.pause()
        print("[skill-system] offers paused. Resume with: skill-offer --resume")
        return 0

    if mode == "resume":
        offer_gate.resume()
        print("[skill-system] offers resumed.")
        return 0

    if mode == "status":
        paused = offer_gate.is_paused()
        print(f"paused: {paused}")
        all_state = offer_gate._load_raw()  # noqa: SLF001 (read-only)
        if not all_state:
            print("(no agent_tools have offer state yet)")
            return 0
        for tool, rec in sorted(all_state.items()):
            if not isinstance(rec, dict):
                continue
            st = rec.get("state", "idle")
            last_offer = rec.get("last_offer_at") or "-"
            last_create = rec.get("last_create_at") or "-"
            print(f"  {tool:<14} state={st:<9} last_offer={last_offer} last_create={last_create}")
        return 0

    if mode == "decline":
        tool = agent_tool or "opencode"
        offer_gate.record_decline(tool)
        print(f"[skill-system] declined for {tool}; cooldown in effect.")
        return 0

    if mode == "reset":
        offer_gate.reset(agent_tool)
        scope = agent_tool or "all agent_tools"
        print(f"[skill-system] reset offer state for {scope}.")
        return 0

    return 1
