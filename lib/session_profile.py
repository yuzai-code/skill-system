"""SessionProfile — portable schema for "what happened in a session".

All Collectors (per-CLI adapters + stdin_json) produce this same schema.
The OfferGate consumes it. Keeping the schema CLI-agnostic is what lets
the core logic stay single-source while adapters vary.

Schema (v1):

    {
      "schema_version": 1,
      "session_id":   str,        # cli-native id or uuid
      "agent_tool":   str,        # "claude-code" | "opencode" | "codex" | custom
      "started_at":   ISO8601 str | None,
      "ended_at":     ISO8601 str | None,
      "tool_calls":   int,        # total tool invocations
      "distinct_tools": [str],    # unique tool names used
      "errors_encountered": int,  # tool results flagged as error
      "error_recoveries": int,    # error followed by a successful retry (heuristic)
      "user_corrections": int,    # user msgs that look like corrections
      "turns":        int,        # agent response turns completed
      "signals":      {}          # extensible per-Collector data (gate ignores)
    }

Accumulation: adapters that stream events (PostToolUse hooks) build a
profile incrementally via SessionProfileBuilder, then flush at session end.
Adapters that parse a transcript post-hoc use parse_profile() directly.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    return uuid.uuid4().hex


@dataclass
class SessionProfile:
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    agent_tool: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    tool_calls: int = 0
    distinct_tools: list[str] = field(default_factory=list)
    errors_encountered: int = 0
    error_recoveries: int = 0
    user_corrections: int = 0
    turns: int = 0
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "agent_tool": self.agent_tool,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "tool_calls": self.tool_calls,
            "distinct_tools": list(self.distinct_tools),
            "errors_encountered": self.errors_encountered,
            "error_recoveries": self.error_recoveries,
            "user_corrections": self.user_corrections,
            "turns": self.turns,
            "signals": dict(self.signals),
        }


class ValidationError(ValueError):
    pass


def validate(d: dict[str, Any]) -> SessionProfile:
    """Coerce a raw dict into a validated SessionProfile.

    Tolerant on missing fields (default 0 / empty) so adapters that only
    collect partial signals still work. Strict on types where it matters.
    """
    if not isinstance(d, dict):
        raise ValidationError("profile must be a JSON object")
    v = d.get("schema_version", SCHEMA_VERSION)
    if int(v) != SCHEMA_VERSION:
        raise ValidationError(
            f"unsupported schema_version {v} (expected {SCHEMA_VERSION})"
        )

    def _int(key: str) -> int:
        try:
            return int(d.get(key, 0) or 0)
        except (TypeError, ValueError):
            raise ValidationError(f"{key} must be an integer")

    tools = d.get("distinct_tools") or []
    if not isinstance(tools, list):
        raise ValidationError("distinct_tools must be a list")
    signals = d.get("signals") or {}
    if not isinstance(signals, dict):
        raise ValidationError("signals must be an object")

    return SessionProfile(
        schema_version=SCHEMA_VERSION,
        session_id=str(d.get("session_id") or new_session_id()),
        agent_tool=str(d.get("agent_tool") or "unknown"),
        started_at=d.get("started_at"),
        ended_at=d.get("ended_at"),
        tool_calls=_int("tool_calls"),
        distinct_tools=[str(t) for t in tools],
        errors_encountered=_int("errors_encountered"),
        error_recoveries=_int("error_recoveries"),
        user_corrections=_int("user_corrections"),
        turns=_int("turns"),
        signals=signals,
    )


def parse_profile(content: str) -> SessionProfile:
    """Parse a JSON profile string (from stdin or file)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValidationError(f"profile is not valid JSON: {e}") from e
    return validate(data)


def load_profile(path: Path) -> SessionProfile:
    try:
        return parse_profile(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValidationError(f"cannot read profile {path}: {e}") from e


def dump_profile(p: SessionProfile, path: Path) -> None:
    """Write profile atomically (used by streaming adapters to flush)."""
    from . import atomic_io
    atomic_io.atomic_write_text(path, json.dumps(p.to_dict(), indent=2, ensure_ascii=False))


class SessionProfileBuilder:
    """Incremental builder for streaming adapters.

    PostToolUse / tool.execute.after hooks call record_tool_call() per event;
    session-end hook calls finalize() to flush a complete profile.

    Recovery heuristic: an error result on tool X followed within the same
    session by a successful result on a tool whose name matches X (or a
    sibling edit/read) counts as one error_recovery.
    """

    def __init__(self, agent_tool: str, session_id: str = "") -> None:
        self.agent_tool = agent_tool
        self.session_id = session_id or new_session_id()
        self.started_at = _now_iso()
        self._tool_calls = 0
        self._distinct: set[str] = set()
        self._errors = 0
        self._recoveries = 0
        self._corrections = 0
        self._turns = 0
        self._last_error_tool: Optional[str] = None
        self._signals: dict[str, Any] = {}

    def record_tool_call(self, tool: str, *, error: bool = False) -> None:
        self._tool_calls += 1
        self._distinct.add(tool)
        if error:
            self._errors += 1
            self._last_error_tool = tool
        elif self._last_error_tool is not None:
            if _tools_related(self._last_error_tool, tool):
                self._recoveries += 1
            self._last_error_tool = None

    def record_user_correction(self) -> None:
        self._corrections += 1

    def record_turn(self) -> None:
        self._turns += 1

    def add_signal(self, key: str, value: Any) -> None:
        self._signals[key] = value

    def finalize(self) -> SessionProfile:
        return SessionProfile(
            schema_version=SCHEMA_VERSION,
            session_id=self.session_id,
            agent_tool=self.agent_tool,
            started_at=self.started_at,
            ended_at=_now_iso(),
            tool_calls=self._tool_calls,
            distinct_tools=sorted(self._distinct),
            errors_encountered=self._errors,
            error_recoveries=self._recoveries,
            user_corrections=self._corrections,
            turns=self._turns,
            signals=self._signals,
        )


def _tools_related(a: str, b: str) -> bool:
    """Heuristic: are two tool names the same operation (retry / recovery)?

    Matches exact name, or same prefix before '-'/'_'/'.' (e.g.
    edit_file / edit_file_v2). Conservative: false negatives are fine,
    false positives inflate recovery counts.
    """
    if not a or not b or a in ("unknown",) or b in ("unknown",):
        return False
    if a == b:
        return True
    pa = a.replace("_", "-").replace(".", "-").split("-")[0]
    pb = b.replace("_", "-").replace(".", "-").split("-")[0]
    return pa == pb and pa not in ("", "unknown")
