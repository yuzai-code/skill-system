#!/usr/bin/env bash
# Claude Code Stop hook: trigger curator maybe-run on task end.
# Inactivity-triggered: only runs if interval (7d default) elapsed.
set -euo pipefail

SKILLS_DIR="${HOME}/.claude/skills"

if [[ ! -d "${SKILLS_DIR}" ]]; then
  exit 0
fi

SKILLS_DIR="${SKILLS_DIR}" python3 "${HOME}/.skill-system/bin/skill-curator" --maybe-run 2>&1 | head -1 || true

exit 0
