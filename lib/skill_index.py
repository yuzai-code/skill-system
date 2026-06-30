"""Skill index — generate a compact, routable index for prompt injection.

Truncates descriptions to MAX_DESCRIPTION_LENGTH (60 chars) so the system
prompt stays bounded. The agent sees this index in every conversation
(when injected via UserPromptSubmit hook) and matches by trigger keywords.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import schema
from .skill_usage import UsageStore


def build_index(skills_dir: Path) -> list[dict[str, Any]]:
    """Return list of {name, description, triggers, category, state, last_used} per skill."""
    if not skills_dir.exists():
        return []
    usage = UsageStore(skills_dir)
    usage_data = usage.load()
    out: list[dict[str, Any]] = []
    for skill_md in skills_dir.rglob("SKILL.md"):
        if any(part in (".archive", ".hub", ".usage") for part in skill_md.parts):
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = schema.parse(text)
        name = fm.get("name")
        if not name:
            continue
        desc = str(fm.get("description", "")).strip()
        if len(desc) > schema.MAX_DESCRIPTION_LENGTH:
            desc = desc[: schema.MAX_DESCRIPTION_LENGTH - 1] + "…"
        triggers = _extract_triggers(text)
        rec = usage_data.get(name, {})
        out.append({
            "name": name,
            "description": desc,
            "triggers": triggers,
            "category": skill_md.parent.parent.name if skill_md.parent.parent != skills_dir else None,
            "state": rec.get("state", "active"),
            "pinned": rec.get("pinned", False),
            "last_used_at": rec.get("last_used_at"),
        })
    out.sort(key=lambda r: (r["state"] != "active", r["name"]))
    return out


def _extract_triggers(text: str) -> list[str]:
    """Extract trigger phrases from ## When to Use section.

    Looks for bullet points under '## When to Use' heading.
    """
    m = re.search(
        r"##\s*When to Use\s*\n+(.*?)(?=\n##\s|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if not m:
        return []
    section = m.group(1)
    triggers: list[str] = []
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith(("-", "*", "•")):
            phrase = line.lstrip("-*•").strip()
            if phrase:
                triggers.append(phrase[:80])
        elif line and not line.startswith("#"):
            triggers.append(line[:80])
        if len(triggers) >= 8:
            break
    return triggers


def render_for_prompt(skills_dir: Path, max_skills: int = 50) -> str:
    """Render a compact index suitable for system-prompt injection.

    Format: one line per skill, name + truncated description.
    Triggers are included inline so the model can match keywords.
    """
    items = build_index(skills_dir)
    active = [i for i in items if i["state"] != "archived"][:max_skills]
    if not active:
        return "(no skills available)"
    lines = ["# Skill Index", ""]
    for item in active:
        line = f"- **{item['name']}**: {item['description']}"
        if item.get("category"):
            line += f"  [category: {item['category']}]"
        lines.append(line)
    if len(items) > len(active):
        lines.append(f"\n({len(items) - len(active)} more skills archived)")
    return "\n".join(lines)


def render_index_json(skills_dir: Path) -> dict[str, Any]:
    """Return full index as JSON-serializable dict."""
    items = build_index(skills_dir)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "active": sum(1 for i in items if i["state"] == "active"),
        "stale": sum(1 for i in items if i["state"] == "stale"),
        "archived": sum(1 for i in items if i["state"] == "archived"),
        "skills": items,
    }
