"""Codex CLI adapter — log-based collector + on_task_complete injection.

Codex hooks.json exposes events on_tool_call and on_task_complete with
action:custom (run a command) / action:log (append to a log file).

Collector strategy: on_tool_call writes one line per call to
~/.skill-system/state/codex_tool.log. on_task_complete runs
`skill-profile --from-log` (this module's parser) to fold the log into a
SessionProfile, evaluate the gate, and emit the offer.

Injector: on_task_complete's custom command writes the offer to a file
Codex picks up, or prints to stdout (Codex injects custom-command stdout
into the next turn — to be verified per Codex version; fallback is a
state file the agent checks via AGENTS.md).

This module exposes parse_log() used by the Codex custom command.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..session_profile import SessionProfile, SessionProfileBuilder, new_session_id

logger = logging.getLogger(__name__)


def parse_log(
    log_path: Path,
    *,
    session_id: str = "",
    agent_tool: str = "codex",
) -> SessionProfile:
    """Parse a Codex on_tool_call log into a SessionProfile.

    Each log line is either:
      {"tool": "<name>", "error": false}                 # from on_tool_call log action
      {"tool": "<name>", "error": true}                  # error variant
      {"kind": "correction"}                              # optional correction marker
    Lines that don't parse as JSON are skipped.
    """
    builder = SessionProfileBuilder(agent_tool=agent_tool, session_id=session_id or new_session_id())
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("cannot read codex tool log %s: %s", log_path, e)
        return builder.finalize()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("kind") == "correction":
            builder.record_user_correction()
            continue
        tool = str(rec.get("tool") or "unknown")
        err = bool(rec.get("error"))
        builder.record_tool_call(tool, error=err)
    return builder.finalize()


def default_log_path() -> Path:
    from ..paths import system_root
    return system_root() / "state" / "codex_tool.log"


def append_tool_event(tool: str, *, error: bool) -> None:
    """Helper for a small CLI entry to append to the log (used by the
    Codex custom command wired to on_tool_call)."""
    from .. import atomic_io
    p = default_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"tool": tool, "error": error}) + "\n"
    # append (atomic_io only does full writes; use plain append for a log)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)
