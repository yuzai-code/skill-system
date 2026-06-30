# Skill System

You have a native `skill_manage` MCP tool (6 actions) for procedural memory.

After complex tasks (5+ tool calls), tricky errors, or novel workflows,
save the approach with `skill_manage(action="create")`. When a skill is
wrong or outdated, patch it immediately with `skill_manage(action="patch")`.

Use `/learn <description>` to author a skill from current context.
Hard constraints (60-char description, author = "hermes-skill-system",
8-section body, tool framing) are documented in the `/learn` command —
load it when authoring new skills.

Do NOT invoke `skill_manage` via Bash — it's a native MCP tool, call it directly.

{{SKILL_INDEX}}
