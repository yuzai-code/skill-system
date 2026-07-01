"""OfferGate — complexity threshold + anti-nag cooldown state machine.

Pure logic (no I/O side effects) plus a small OfferStateStore for cooldown
persistence. The gate decides whether a SessionProfile merits offering the
user "save this as a skill". The cooldown prevents re-offering the same
session pattern within a window.

Rules (default thresholds, env-overridable):

    tool_calls              >= 5       complex multi-step task
    error_recoveries        >= 1       recovered from a failure = high-value
    user_corrections        >= 1       user corrected the path = error-prone
    distinct_tools AND      >= 4 and
    tool_calls              >= 3       multi-tool combination

Any one rule firing => should_offer=True (logical OR).

Cooldown state machine (per agent_tool):

    IDLE
      -> profile meets threshold -> emit offer -> WAITING
    WAITING
      -> skill_manage(create) called   -> COOLDOWN (default 24h)
      -> timeout (default 1h) no create -> IDLE
    COOLDOWN
      -> expires -> IDLE

State lives in ~/.skill-system/state/offer_state.json, keyed by agent_tool.
One file across all CLIs; the store uses atomic writes + fcntl lock.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    import fcntl
except ImportError:  # non-POSIX fallback
    fcntl = None  # type: ignore

from .paths import system_root
from .session_profile import SessionProfile

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_COOLDOWN = "cooldown"
_VALID_STATES = {STATE_IDLE, STATE_WAITING, STATE_COOLDOWN}

# Default thresholds (env-overridable; see _env_int)
DEFAULT_TOOL_CALLS = 5
DEFAULT_ERROR_RECOVERIES = 1
DEFAULT_USER_CORRECTIONS = 1
DEFAULT_DISTINCT_TOOLS = 4
DEFAULT_DISTINCT_TOOL_CALLS = 3

# Default cooldown windows
DEFAULT_WAITING_TIMEOUT_HOURS = 1      # offer expires if no create within 1h
DEFAULT_COOLDOWN_HOURS = 24            # don't re-offer same tool within 24h


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        logger.debug("env %s=%r not an int, using default %d", name, v, default)
        return default


def _env_hours(name: str, default: float) -> float:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class GateResult:
    should_offer: bool
    reason: str
    score: int
    rules_fired: list[str]
    blocked_by_cooldown: bool = False


def score(p: SessionProfile) -> int:
    """Numeric complexity score. Higher = more complex.

    Weighted sum used only for ranking/debugging; the gate uses rule
    thresholds, not the score, to decide. Kept here so callers can sort
    sessions by complexity if needed.
    """
    return (
        p.tool_calls
        + 2 * len(p.distinct_tools)
        + 3 * p.errors_encountered
        + 4 * p.error_recoveries
        + 3 * p.user_corrections
        + p.turns
    )


def evaluate_rules(p: SessionProfile) -> tuple[bool, list[str], str]:
    """Apply the 4 threshold rules. Returns (fired, rules_fired, reason).

    Pure function, no cooldown check.
    """
    min_tools = _env_int("SKILL_OFFER_MIN_TOOL_CALLS", DEFAULT_TOOL_CALLS)
    min_recoveries = _env_int("SKILL_OFFER_MIN_RECOVERIES", DEFAULT_ERROR_RECOVERIES)
    min_corrections = _env_int("SKILL_OFFER_MIN_CORRECTIONS", DEFAULT_USER_CORRECTIONS)
    min_distinct = _env_int("SKILL_OFFER_MIN_DISTINCT_TOOLS", DEFAULT_DISTINCT_TOOLS)
    min_distinct_calls = _env_int(
        "SKILL_OFFER_MIN_DISTINCT_TOOL_CALLS", DEFAULT_DISTINCT_TOOL_CALLS
    )

    fired: list[str] = []
    parts: list[str] = []

    if p.tool_calls >= min_tools:
        fired.append("tool_calls")
        parts.append(f"{p.tool_calls} tool calls")
    if p.error_recoveries >= min_recoveries:
        fired.append("error_recoveries")
        parts.append(f"{p.error_recoveries} recoveries")
    if p.user_corrections >= min_corrections:
        fired.append("user_corrections")
        parts.append(f"{p.user_corrections} corrections")
    if (
        len(p.distinct_tools) >= min_distinct
        and p.tool_calls >= min_distinct_calls
    ):
        fired.append("distinct_tools")
        parts.append(f"{len(p.distinct_tools)} distinct tools")

    if not fired:
        return False, [], "below threshold"
    return True, fired, "; ".join(parts)


# ---------- Cooldown state store ----------

def _state_dir() -> Path:
    d = system_root() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file() -> Path:
    return _state_dir() / "offer_state.json"


def _lock_file() -> Path:
    return _state_dir() / "offer_state.json.lock"


def _paused_file() -> Path:
    """Empty file presence = globally paused (all agent_tools)."""
    return _state_dir() / "offer_paused"


def is_paused() -> bool:
    return _paused_file().exists()


def pause() -> None:
    _paused_file().touch()


def resume() -> None:
    try:
        _paused_file().unlink()
    except FileNotFoundError:
        pass


@contextmanager
def _lock() -> Iterator[None]:
    lock_path = _lock_file()
    if fcntl is None:  # non-POSIX: best-effort, no lock
        yield
        return
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _load_raw() -> dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("failed to read offer state: %s", e)
        return {}


def _save_raw(data: dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix=".offer_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _empty_entry() -> dict[str, Any]:
    return {
        "state": STATE_IDLE,
        "last_offer_at": None,
        "last_create_at": None,
        "last_profile": None,
        "session_id": None,
    }


def get_state(agent_tool: str) -> dict[str, Any]:
    """Read cooldown state for one agent_tool. Fills defaults."""
    with _lock():
        data = _load_raw()
    rec = data.get(agent_tool)
    if not isinstance(rec, dict):
        return _empty_entry()
    base = _empty_entry()
    base.update(rec)
    return base


def _transition(agent_tool: str, mutate) -> dict[str, Any]:
    """Read-modify-write under lock. mutate(data) returns new record dict."""
    with _lock():
        data = _load_raw()
        rec = data.get(agent_tool)
        if not isinstance(rec, dict):
            rec = _empty_entry()
        else:
            base = _empty_entry()
            base.update(rec)
            rec = base
        new_rec = mutate(rec)
        if new_rec is not None:
            data[agent_tool] = new_rec
            _save_raw(data)
        return new_rec if new_rec is not None else rec


def record_offer(agent_tool: str, profile: SessionProfile) -> dict[str, Any]:
    """Called by Injector after emitting an offer. Moves IDLE -> WAITING."""

    def m(rec: dict[str, Any]) -> dict[str, Any]:
        rec["state"] = STATE_WAITING
        rec["last_offer_at"] = _now().isoformat()
        rec["last_profile"] = profile.to_dict()
        rec["session_id"] = profile.session_id
        return rec

    return _transition(agent_tool, m)


def record_create(agent_tool: str) -> dict[str, Any]:
    """Called when skill_manage(create) fires for this agent_tool.
    Moves WAITING -> COOLDOWN. Callers should invoke from the create path
    (hook or MCP server) for the active agent_tool."""

    def m(rec: dict[str, Any]) -> dict[str, Any]:
        rec["state"] = STATE_COOLDOWN
        rec["last_create_at"] = _now().isoformat()
        return rec

    return _transition(agent_tool, m)


def record_decline(agent_tool: str) -> dict[str, Any]:
    """Called when user/agent declines or offer times out.
    Moves WAITING -> COOLDOWN (shorter) so we don't re-offer immediately."""

    def m(rec: dict[str, Any]) -> dict[str, Any]:
        rec["state"] = STATE_COOLDOWN
        rec["last_create_at"] = None  # declined, not created
        rec["last_offer_at"] = _now().isoformat()
        return rec

    return _transition(agent_tool, m)


def _expire(rec: dict[str, Any]) -> dict[str, Any]:
    """Apply time-based transitions in place. Returns (possibly mutated) rec."""
    now = _now()
    state = rec.get("state", STATE_IDLE)
    if state == STATE_WAITING:
        last_offer = _parse_iso(rec.get("last_offer_at"))
        timeout = timedelta(hours=_env_hours("SKILL_OFFER_WAITING_TIMEOUT_HOURS", DEFAULT_WAITING_TIMEOUT_HOURS))
        if last_offer and (now - last_offer) >= timeout:
            rec["state"] = STATE_IDLE
            rec["last_offer_at"] = None
            rec["last_profile"] = None
            rec["session_id"] = None
    elif state == STATE_COOLDOWN:
        anchor = _parse_iso(rec.get("last_create_at") or rec.get("last_offer_at"))
        cooldown = timedelta(hours=_env_hours("SKILL_OFFER_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS))
        if anchor and (now - anchor) >= cooldown:
            rec["state"] = STATE_IDLE
            rec["last_offer_at"] = None
            rec["last_create_at"] = None
            rec["last_profile"] = None
            rec["session_id"] = None
    return rec


def evaluate(p: SessionProfile) -> GateResult:
    """Full gate: rules + cooldown + global pause. Main entry for adapters."""
    if is_paused():
        return GateResult(
            should_offer=False,
            reason="offers paused (skill-offer --resume to re-enable)",
            score=0,
            rules_fired=[],
            blocked_by_cooldown=False,
        )
    # Apply time-based expiry first (cheap, reads current state)
    rec = get_state(p.agent_tool)
    rec = _expire(rec)
    # If expiry changed state, persist it
    current_state = rec.get("state", STATE_IDLE)
    if current_state != get_state(p.agent_tool).get("state", STATE_IDLE):
        def m(r: dict[str, Any]) -> dict[str, Any]:
            return _expire(r)
        _transition(p.agent_tool, m)

    fired, rules, reason = evaluate_rules(p)
    s = score(p)

    if not fired:
        return GateResult(
            should_offer=False,
            reason=f"below threshold ({reason})",
            score=s,
            rules_fired=[],
            blocked_by_cooldown=False,
        )

    if current_state in (STATE_WAITING, STATE_COOLDOWN):
        return GateResult(
            should_offer=False,
            reason=f"rules fired ({reason}) but {current_state} (cooldown)",
            score=s,
            rules_fired=rules,
            blocked_by_cooldown=True,
        )

    return GateResult(
        should_offer=True,
        reason=reason,
        score=s,
        rules_fired=rules,
        blocked_by_cooldown=False,
    )


def reset(agent_tool: Optional[str] = None) -> None:
    """Clear state for one agent_tool, or all if None. For tests / manual."""
    with _lock():
        data = _load_raw()
        if agent_tool is None:
            data = {}
        else:
            data.pop(agent_tool, None)
        _save_raw(data)
