#!/usr/bin/env bash
# Claude Code PostToolUse hook: refresh skill index after skill_manage.
# Re-generates the index cache so subsequent UserPromptSubmit picks up changes.
set -euo pipefail

SYSTEM_ROOT="${HOME}/.skill-system"

# Detect CLI: CodeFuse vs Claude Code
if [[ -n "${CODEFUSE_FUSE_DIR:-}" ]] || [[ -n "${CODEFUSE_SESSION:-}" ]]; then
  SKILLS_DIR="${HOME}/.codefuse/fuse/skills"
else
  SKILLS_DIR="${HOME}/.claude/skills"
fi

if [[ ! -d "${SKILLS_DIR}" ]]; then
  exit 0
fi

# Best-effort: refresh index; never fail the hook.
python3 -c "
import sys
sys.path.insert(0, '${SYSTEM_ROOT}')
try:
    from lib import skill_index
    print(skill_index.render_for_prompt(__import__('pathlib').Path('${SKILLS_DIR}')), end='')
except Exception as e:
    sys.stderr.write(f'index refresh failed: {e}\n')
" > "${SKILLS_DIR}/.index.cache" 2>/dev/null || true

exit 0
