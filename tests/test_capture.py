#!/usr/bin/env python3
"""Tests for the automatic skill-capture (left half) layer.

Covers:
  1. session_profile: parse / validate / builder / recovery heuristic
  2. offer_gate: 4 threshold rules + cooldown state machine + expiry + pause
  3. offer_message: render carries quantified reason + profile fields
  4. CLI: skill-profile emits / blocks / records; skill-offer manages state

Run: python3 ~/.skill-system/tests/test_capture.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_ROOT))

# Isolate state to a temp HOME so tests don't touch the user's real
# ~/.skill-system/state. Must set before importing lib (paths reads HOME).
_TMP_HOME = Path(tempfile.mkdtemp(prefix="skill-capture-test-"))
os.environ["HOME"] = str(_TMP_HOME)
os.environ["PATH"] = str(SYSTEM_ROOT / "bin") + os.pathsep + os.environ["PATH"]

from lib import offer_gate, offer_message, offer_runner  # noqa: E402
from lib.session_profile import (  # noqa: E402
    SessionProfile,
    SessionProfileBuilder,
    ValidationError,
    parse_profile,
    _tools_related,
)

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
PASS = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    raise SystemExit(1)


def section(name: str) -> None:
    print(f"\n=== {name} ===")


def _reset() -> None:
    offer_gate.reset()
    offer_gate.resume()


def _profile(**kw) -> SessionProfile:
    base = dict(agent_tool="opencode", session_id="test")
    base.update(kw)
    return SessionProfile(**base)


# ---------- 1. session_profile ----------

def test_session_profile() -> None:
    section("session_profile")

    p = _profile(tool_calls=5, distinct_tools=["read", "edit"])
    d = p.to_dict()
    ok("to_dict round-trips schema_version")
    assert d["schema_version"] == 1
    assert d["tool_calls"] == 5

    p2 = parse_profile(json.dumps(d))
    ok("parse_profile inverts to_dict")
    assert p2.tool_calls == 5 and p2.agent_tool == "opencode"

    try:
        parse_profile("{not json")
        fail("bad JSON should raise")
    except ValidationError:
        ok("malformed JSON rejected")

    try:
        parse_profile(json.dumps({"schema_version": 99, "agent_tool": "x"}))
        fail("bad schema_version should raise")
    except ValidationError:
        ok("unsupported schema_version rejected")

    # missing fields default leniently
    p3 = parse_profile(json.dumps({"agent_tool": "codex"}))
    ok("missing ints default to 0")
    assert p3.tool_calls == 0 and p3.distinct_tools == []
    assert p3.session_id  # auto-generated

    # builder accumulation
    b = SessionProfileBuilder(agent_tool="opencode", session_id="b1")
    b.record_tool_call("read")
    b.record_tool_call("edit", error=True)
    b.record_tool_call("edit")  # recovery: same tool after error
    b.record_tool_call("bash")
    b.record_turn()
    fin = b.finalize()
    ok("builder counts tool_calls")
    assert fin.tool_calls == 4
    assert fin.errors_encountered == 1
    assert fin.turns == 1
    ok("builder detects error recovery (same-tool retry)")
    assert fin.error_recoveries == 1, f"expected 1 recovery, got {fin.error_recoveries}"
    assert "edit" in fin.distinct_tools and "bash" in fin.distinct_tools

    # recovery heuristic: unrelated tool after error is NOT a recovery
    b2 = SessionProfileBuilder(agent_tool="x", session_id="b2")
    b2.record_tool_call("edit", error=True)
    b2.record_tool_call("bash")  # different operation, not a retry
    assert b2.finalize().error_recoveries == 0
    ok("builder rejects unrelated-tool recovery")

    # _tools_related helper
    assert _tools_related("edit_file", "edit_file")
    assert _tools_related("read_file", "read_file_v2")
    assert not _tools_related("edit_file", "bash")
    assert not _tools_related("", "")
    ok("_tools_related heuristic correct")


# ---------- 2. offer_gate rules ----------

def test_gate_rules() -> None:
    section("offer_gate rules")
    _reset()

    # below all thresholds
    r = offer_gate.evaluate(_profile(tool_calls=2))
    ok("below threshold -> no offer")
    assert not r.should_offer and not r.rules_fired
    assert "below threshold" in r.reason

    # tool_calls rule
    r = offer_gate.evaluate(_profile(tool_calls=6, distinct_tools=["read"]))
    ok("tool_calls >= 5 fires")
    assert r.should_offer and "tool_calls" in r.rules_fired

    _reset()
    # error_recoveries rule (alone, below tool_calls threshold)
    r = offer_gate.evaluate(_profile(tool_calls=2, error_recoveries=1))
    ok("error_recoveries >= 1 fires")
    assert r.should_offer and "error_recoveries" in r.rules_fired

    _reset()
    # user_corrections rule
    r = offer_gate.evaluate(_profile(tool_calls=2, user_corrections=1))
    ok("user_corrections >= 1 fires")
    assert r.should_offer and "user_corrections" in r.rules_fired

    _reset()
    # distinct_tools rule: need >= 4 distinct AND >= 3 calls
    r = offer_gate.evaluate(_profile(tool_calls=3, distinct_tools=["a", "b", "c", "d"]))
    ok("distinct_tools >= 4 and tool_calls >= 3 fires")
    assert r.should_offer and "distinct_tools" in r.rules_fired

    _reset()
    # distinct_tools NOT firing when too few calls
    r = offer_gate.evaluate(_profile(tool_calls=2, distinct_tools=["a", "b", "c", "d"]))
    ok("distinct_tools needs >= 3 calls (boundary)")
    assert not r.should_offer

    # env override: raise tool_calls threshold to 10
    os.environ["SKILL_OFFER_MIN_TOOL_CALLS"] = "10"
    _reset()
    r = offer_gate.evaluate(_profile(tool_calls=6))
    ok("env override raises threshold")
    assert not r.should_offer
    del os.environ["SKILL_OFFER_MIN_TOOL_CALLS"]

    # score is monotonic-ish
    s_low = offer_gate.score(_profile(tool_calls=2))
    s_high = offer_gate.score(_profile(tool_calls=10, error_recoveries=2))
    ok("score increases with complexity")
    assert s_high > s_low


# ---------- 3. cooldown state machine ----------

def test_cooldown() -> None:
    section("cooldown state machine")
    _reset()

    # IDLE -> WAITING (via record_offer)
    p = _profile(tool_calls=6)
    offer_gate.record_offer("opencode", p)
    st = offer_gate.get_state("opencode")
    ok("record_offer moves IDLE -> WAITING")
    assert st["state"] == offer_gate.STATE_WAITING

    # WAITING blocks re-offer
    r = offer_gate.evaluate(_profile(tool_calls=20))
    ok("WAITING blocks a new offer")
    assert not r.should_offer and r.blocked_by_cooldown

    # WAITING -> COOLDOWN (record_create)
    offer_gate.record_create("opencode")
    st = offer_gate.get_state("opencode")
    ok("record_create moves WAITING -> COOLDOWN")
    assert st["state"] == offer_gate.STATE_COOLDOWN

    # COOLDOWN blocks
    r = offer_gate.evaluate(_profile(tool_calls=20))
    ok("COOLDOWN blocks offers")
    assert not r.should_offer and r.blocked_by_cooldown

    # backdate last_create_at to expire cooldown
    _backdate("opencode", "last_create_at", hours=25)
    r = offer_gate.evaluate(_profile(tool_calls=6))
    ok("COOLDOWN expiry returns to IDLE and re-offers")
    assert r.should_offer, f"expected re-offer after expiry, got {r.reason}"

    # WAITING timeout -> IDLE (no create within window)
    _reset()
    offer_gate.record_offer("opencode", _profile(tool_calls=6))
    _backdate("opencode", "last_offer_at", hours=2)
    r = offer_gate.evaluate(_profile(tool_calls=6))
    ok("WAITING timeout returns to IDLE")
    assert r.should_offer, f"expected offer after waiting timeout, got {r.reason}"

    # decline moves WAITING -> COOLDOWN (short)
    _reset()
    offer_gate.record_offer("opencode", _profile(tool_calls=6))
    offer_gate.record_decline("opencode")
    st = offer_gate.get_state("opencode")
    ok("decline moves WAITING -> COOLDOWN")
    assert st["state"] == offer_gate.STATE_COOLDOWN

    # per-agent_tool isolation
    _reset()
    offer_gate.record_offer("opencode", _profile(tool_calls=6))
    r = offer_gate.evaluate(_profile(agent_tool="codex", tool_calls=6))
    ok("cooldown is per-agent_tool (codex not blocked by opencode)")
    assert r.should_offer


# ---------- 4. pause / resume ----------

def test_pause_resume() -> None:
    section("pause / resume")
    _reset()
    offer_gate.pause()
    assert offer_gate.is_paused()
    r = offer_gate.evaluate(_profile(tool_calls=20, error_recoveries=2))
    ok("paused gate never offers")
    assert not r.should_offer and "paused" in r.reason
    offer_gate.resume()
    assert not offer_gate.is_paused()
    r = offer_gate.evaluate(_profile(tool_calls=20))
    ok("resumed gate offers again")
    assert r.should_offer


# ---------- 5. offer_message ----------

def test_message() -> None:
    section("offer_message")
    _reset()
    p = _profile(tool_calls=12, error_recoveries=1, user_corrections=2, distinct_tools=["a", "b", "c", "d"])
    r = offer_gate.evaluate(p)
    msg = offer_message.render(p, r)
    ok("render includes quantified reason")
    assert "12 tool calls" in msg
    assert "1 recoveries" in msg
    assert "2 corrections" in msg
    ok("render instructs ask-first (no silent create)")
    assert "Ask the user" in msg
    assert "Only on explicit" in msg
    ok("render mentions hard constraints")
    assert "60 chars" in msg and "skill-system" in msg

    # render_for_adapter structured form
    s = offer_message.render_for_adapter(p, r)
    ok("render_for_adapter returns structured object")
    assert s["message"]  # message present
    assert s["rules_fired"] == r.rules_fired


# ---------- 6. CLI end-to-end ----------

def _run(args, stdin=None):
    return subprocess.run(
        [sys.executable, str(SYSTEM_ROOT / "bin" / args[0])] + args[1:],
        capture_output=True, text=True, input=stdin, timeout=10,
    )

def test_cli() -> None:
    section("CLI end-to-end")
    _reset()

    # skill-profile emits on stdout
    p = _run(["skill-profile", "--from-stdin", "--agent-tool", "opencode"],
             stdin=json.dumps({"agent_tool": "opencode", "tool_calls": 7}))
    ok("skill-profile exits 0 on emit")
    assert p.returncode == 0, p.stderr
    ok("offer message on stdout")
    assert "complexity threshold" in p.stdout
    assert "Ask the user" in p.stdout

    # second call blocked by WAITING
    p2 = _run(["skill-profile", "--from-stdin", "--agent-tool", "opencode"],
              stdin=json.dumps({"agent_tool": "opencode", "tool_calls": 9}))
    ok("second call blocked by WAITING")
    assert p2.returncode == 0
    assert "no offer" in p2.stderr and "waiting" in p2.stderr
    assert "threshold" not in p2.stdout

    # skill-offer --status shows the entry
    s = _run(["skill-offer", "--status"])
    ok("skill-offer --status lists agent_tool")
    assert "opencode" in s.stdout and "waiting" in s.stdout

    # record-create transitions to cooldown
    rc = _run(["skill-profile", "--record-create", "--agent-tool", "opencode"])
    ok("skill-profile --record-create acknowledged")
    assert "cooldown started" in rc.stdout

    # skill-offer --reset clears
    _run(["skill-offer", "--reset"])
    s = _run(["skill-offer", "--status"])
    ok("skill-offer --reset clears state")
    assert "opencode" not in s.stdout

    # --json structured output
    j = _run(["skill-profile", "--from-stdin", "--json"],
             stdin=json.dumps({"agent_tool": "opencode", "tool_calls": 6}))
    ok("--json returns structured object")
    obj = json.loads(j.stdout)
    assert obj["should_offer"] is True
    assert "message" in obj and obj["message"]

    # invalid profile JSON -> non-zero
    bad = _run(["skill-profile", "--from-stdin"], stdin="{not json")
    ok("invalid JSON exits non-zero")
    assert bad.returncode != 0

    # --from-file path
    _run(["skill-offer", "--reset"])
    pf = _TMP_HOME / "profile.json"
    pf.write_text(json.dumps({"agent_tool": "opencode", "tool_calls": 6}))
    rf = _run(["skill-profile", "--from-file", str(pf), "--agent-tool", "opencode"])
    ok("--from-file reads profile from disk")
    assert rf.returncode == 0 and "threshold" in rf.stdout

    # skill-offer --pause / --resume
    _run(["skill-offer", "--pause"])
    p = _run(["skill-profile", "--from-stdin", "--agent-tool", "opencode"],
             stdin=json.dumps({"agent_tool": "opencode", "tool_calls": 50}))
    ok("paused CLI emits nothing")
    assert "threshold" not in p.stdout
    _run(["skill-offer", "--resume"])

    # summarize_rules helper
    ok("summarize_rules maps rule names")
    assert "tool calls" in offer_message.summarize_rules(["tool_calls"])
    assert "error recovery" in offer_message.summarize_rules(["error_recoveries"])
    assert offer_message.summarize_rules([]) == "none"


def _backdate(agent_tool: str, key: str, hours: float) -> None:
    """Backdate a timestamp field in the offer state to simulate expiry."""
    with offer_gate._lock():  # noqa: SLF001
        data = offer_gate._load_raw()  # noqa: SLF001
        rec = data.get(agent_tool)
        if rec and isinstance(rec, dict):
            rec[key] = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            data[agent_tool] = rec
            offer_gate._save_raw(data)  # noqa: SLF001


def main() -> int:
    print(f"state dir: {_TMP_HOME}")
    try:
        test_session_profile()
        test_gate_rules()
        test_cooldown()
        test_pause_resume()
        test_message()
        test_cli()
    finally:
        shutil.rmtree(_TMP_HOME, ignore_errors=True)
    print(f"\n{GREEN}All capture tests passed.{RESET} ({PASS} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
