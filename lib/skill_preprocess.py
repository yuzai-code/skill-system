"""SKILL.md preprocessing — template variables + inline shell.

Template variables (default ON, safe — pure string substitution):
  - ${HERMES_SKILL_DIR} → absolute path of skill directory
  - ${HERMES_SESSION_ID} → session ID if available

Inline shell (default OFF — opt-in, security risk):
  - !`command` → runs `command` with cwd=skill_dir, replaces with stdout
  - timeout 10s, output cap 4000 chars, stdin=DEVNULL
  - failure returns [inline-shell error: ...] marker, never raises

Both are configurable via env vars:
  SKILL_TEMPLATE_VARS=true|false   (default true)
  SKILL_INLINE_SHELL=true|false     (default false)
  SKILL_INLINE_SHELL_TIMEOUT=10     (seconds)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SKILL_TEMPLATE_RE = re.compile(r"\$\{(HERMES_SKILL_DIR|HERMES_SESSION_ID)\}")
_INLINE_SHELL_RE = re.compile(r"!`([^`\n]+)`")
_INLINE_SHELL_MAX_OUTPUT = 4000


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def substitute_template_vars(
    content: str,
    skill_dir: Optional[Path],
    session_id: Optional[str],
) -> str:
    """Replace ${HERMES_SKILL_DIR} and ${HERMES_SESSION_ID} tokens.

    Unresolved tokens are left as-is so the author can debug them.
    """
    if not content:
        return content
    skill_dir_str = str(skill_dir) if skill_dir else None

    def _replace(m: re.Match) -> str:
        token = m.group(1)
        if token == "HERMES_SKILL_DIR" and skill_dir_str:
            return skill_dir_str
        if token == "HERMES_SESSION_ID" and session_id:
            return str(session_id)
        return m.group(0)

    return _SKILL_TEMPLATE_RE.sub(_replace, content)


def run_inline_shell(command: str, cwd: Optional[Path], timeout: int) -> str:
    """Run a single shell snippet. Returns stdout (trimmed) or error marker."""
    try:
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"[inline-shell timeout after {timeout}s: {command}]"
    except FileNotFoundError:
        return "[inline-shell error: bash not found]"
    except Exception as exc:
        return f"[inline-shell error: {exc}]"
    output = (completed.stdout or "").rstrip("\n")
    if not output and completed.stderr:
        output = completed.stderr.rstrip("\n")
    if len(output) > _INLINE_SHELL_MAX_OUTPUT:
        output = output[:_INLINE_SHELL_MAX_OUTPUT] + "...[truncated]"
    return output


def expand_inline_shell(
    content: str, skill_dir: Optional[Path], timeout: int
) -> str:
    """Replace every !`cmd` with its stdout. cwd = skill_dir."""
    if "!`" not in content:
        return content

    def _replace(m: re.Match) -> str:
        cmd = m.group(1).strip()
        if not cmd:
            return ""
        return run_inline_shell(cmd, skill_dir, timeout)

    return _INLINE_SHELL_RE.sub(_replace, content)


def preprocess(
    content: str,
    skill_dir: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> str:
    """Apply configured preprocessing. Honors env-var flags."""
    if not content:
        return content
    if _bool_env("SKILL_TEMPLATE_VARS", True):
        content = substitute_template_vars(content, skill_dir, session_id)
    if _bool_env("SKILL_INLINE_SHELL", False):
        timeout = _int_env("SKILL_INLINE_SHELL_TIMEOUT", 10)
        content = expand_inline_shell(content, skill_dir, timeout)
    return content
