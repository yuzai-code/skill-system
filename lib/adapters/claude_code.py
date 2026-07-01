"""Claude Code adapter — transcript parser + Stop-hook injection.

Collector: parse a Claude Code transcript JSONL file into a SessionProfile.
  Claude Code's Stop hook receives JSON on stdin with a `transcript_path`
  field pointing at a JSONL log of the session. Each line is a message
  record. Tool calls appear as assistant messages with tool_use content
  blocks; errors appear as tool_result blocks with is_error=true.

Injector: the Stop hook prints the OfferMessage to stdout. Claude Code
  includes Stop-hook stdout in the next turn's context, so the agent sees
  the offer at the start of its next response.

This module exposes collect_from_transcript() used by the Stop hook.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..session_profile import SessionProfile, SessionProfileBuilder, new_session_id

logger = logging.getLogger(__name__)


def collect_from_transcript(
    transcript_path: Path,
    *,
    session_id: str = "",
    agent_tool: str = "claude-code",
) -> SessionProfile:
    """Parse a Claude Code transcript JSONL into a SessionProfile.

    Tolerant of format drift: any unparseable line is skipped. Tool names
    are extracted from assistant tool_use blocks; errors from tool_result
    blocks with is_error=true. Recovery heuristic uses the builder's
    same-tool-after-error detection.
    """
    builder = SessionProfileBuilder(agent_tool=agent_tool, session_id=session_id or new_session_id())
    try:
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("cannot read transcript %s: %s", transcript_path, e)
        return builder.finalize()

    last_assistant_tool: str | None = None
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
        role = rec.get("type") or rec.get("role")
        message = rec.get("message") if isinstance(rec.get("message"), dict) else rec

        # Detect assistant tool_use blocks
        if role in ("assistant", "Assistant"):
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool = str(block.get("name") or "unknown")
                        builder.record_tool_call(tool, error=False)
                        last_assistant_tool = tool
            builder.record_turn()

        # Detect tool_result blocks (user role carrying tool results)
        elif role in ("user", "User"):
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        # The matching tool_use was already counted above; we
                        # only flag an error here if the result is an error.
                        is_err = bool(block.get("is_error")) or _looks_like_error(block)
                        if is_err and last_assistant_tool:
                            # Retroactively mark the last tool call as errored.
                            # Builder has no un-mark; approximate by recording
                            # an extra error + clearing recovery cursor.
                            builder._errors += 1  # noqa: SLF001
                            builder._last_error_tool = last_assistant_tool  # noqa: SLF001
            # Heuristic user correction: short imperative follow-up after a long agent turn
            text_content = _extract_text(content) if isinstance(content, list) else ""
            if text_content and _looks_like_correction(text_content):
                builder.record_user_correction()

    return builder.finalize()


_ERROR_RE = re.compile(r"\b(error|fail|wrong|no\b|denied|not found|exception)\b", re.IGNORECASE)
_CORRECTION_RE = re.compile(
    r"^(no|wait|actually|instead|don't|do not|retry|fix|wrong|oops|stop)\b",
    re.IGNORECASE,
)


def _looks_like_error(block: dict[str, Any]) -> bool:
    content = block.get("content")
    if isinstance(content, str) and _ERROR_RE.search(content):
        return True
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                if _ERROR_RE.search(c["text"]):
                    return True
    return False


def _extract_text(content: list) -> str:
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            return block["text"]
    return ""


def _looks_like_correction(text: str) -> bool:
    return bool(_CORRECTION_RE.match(text.strip())) and len(text) < 200
