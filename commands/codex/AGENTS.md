# Skill System

You have a native `skill_manage` MCP tool (6 actions) for procedural memory.

Use `/learn <description>` to author a skill from current context.
Hard constraints (60-char description, author = "skill-system",
8-section body, tool framing) are documented in the `/learn` command —
load it when authoring new skills.

When a skill is wrong or outdated, patch it with `skill_manage(action="patch")`.
Do NOT invoke `skill_manage` via Bash — it's a native MCP tool, call it directly.

Skill-capture offers are injected automatically when a session meets the
complexity threshold — follow the injected instructions (ask the user
first; only create on explicit "yes"). No need to self-monitor task
complexity in the common case.

{{SKILL_INDEX}}
