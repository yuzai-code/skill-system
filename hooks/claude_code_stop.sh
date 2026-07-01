#!/usr/bin/env bash
# Claude Code Stop hook.
#   1. Collect a SessionProfile from the just-ended session's transcript.
#   2. Run the OfferGate; if it fires, emit the OfferMessage to stdout.
#      Claude Code includes Stop-hook stdout in the next turn's context,
#      so the agent sees the offer at the start of its next response.
#   3. Trigger the curator maybe-run (inactivity-triggered maintenance).
# All best-effort: hook failures never block the session.
set -euo pipefail

SYSTEM_ROOT="${HOME}/.skill-system"

# Detect which CLI is running: CodeFuse sets CODEFUSE_FUSE_DIR or has
# ~/.codefuse/fuse in the environment; Claude Code sets CLAUDE_PROJECT_DIR.
if [[ -n "${CODEFUSE_FUSE_DIR:-}" ]] || [[ -n "${CODEFUSE_SESSION:-}" ]]; then
  SKILLS_DIR="${HOME}/.codefuse/fuse/skills"
  AGENT_TOOL="codefuse"
else
  SKILLS_DIR="${HOME}/.claude/skills"
  AGENT_TOOL="claude-code"
fi

# Read Stop-hook stdin JSON (contains transcript_path + session_id).
STDIN_JSON="${STDIN_JSON:-}"
if [[ -z "${STDIN_JSON}" ]]; then
  STDIN_JSON="$(cat 2>/dev/null || true)"
fi

# Auto-capture: build profile from transcript, run gate, emit offer.
export AGENT_TOOL
if [[ -n "${STDIN_JSON}" ]]; then
  # Write stdin to a temp file so Python can parse it safely (avoid quoting hell).
  PROFILE_TMP="$(mktemp -t cc_stop_XXXXXX.json)"
  printf '%s' "${STDIN_JSON}" > "${PROFILE_TMP}"
  python3 - "${PROFILE_TMP}" <<'PYEOF' 2>/dev/null || true
import json, sys, os, tempfile, subprocess
from pathlib import Path
system_root = os.environ["HOME"] + "/.skill-system"
sys.path.insert(0, system_root)
agent_tool = os.environ.get("AGENT_TOOL", "claude-code")
stdin_file = Path(sys.argv[1])
if tp and Path(tp).exists():
    p = collect_from_transcript(Path(tp), session_id=sid, agent_tool=agent_tool)
    dump_path = Path(tempfile.gettempdir()) / f"cc_profile_{sid or 'x'}.json"
    dump_profile(p, dump_path)
    r = subprocess.run(
        [sys.executable, f"{system_root}/bin/skill-profile",
         "--from-file", str(dump_path), "--agent-tool", agent_tool],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0 and "complexity threshold" in r.stdout:
        sys.stdout.write(r.stdout)
PYEOF
fi

# Curator: inactivity-triggered maintenance (7d default).
if [[ -d "${SKILLS_DIR}" ]]; then
  SKILLS_DIR="${SKILLS_DIR}" python3 "${SYSTEM_ROOT}/bin/skill-curator" --maybe-run 2>&1 | head -1 || true
fi

exit 0
