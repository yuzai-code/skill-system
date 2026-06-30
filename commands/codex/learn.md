---
description: Author a reusable skill from described sources.
argument-hint: <workflow description, paths, URLs, or "what we just did">
---

# /learn — Skill Authoring

The user wants a reusable skill saved. Resolve what they described:

- `read_file` / `search_files` for local files or directories
- `web_extract` for URLs
- Current conversation history for "what we just did"
- Pasted text as-is

Then author ONE SKILL.md via `skill_manage(action="create")`.

## Authoring Standards (HARDLINE)

### Frontmatter (REQUIRED)

```yaml
---
name: <slug>                    # kebab-case, ≤64 chars
description: <one sentence>     # HARD: ≤60 characters
version: 0.1.0
author: hermes-skill-system     # literal value, NEVER environment identity
platforms: [macos, linux]       # optional, OS-bound only
metadata:
  hermes:
    tags: [Tag1, Tag2]
---
```

**description ≤ 60 characters** — system index truncates; longer never routes.

**author = `hermes-skill-system`** — never OS username or git config;
privacy: skills get shared and published.

**platforms** only when OS-bound primitive required. Prefer cross-platform.

### Body (8 sections)

1. `# <Human Title>` + 2-3 sentence intro
2. `## When to Use` — trigger phrases
3. `## Prerequisites` — env vars, install, credentials
4. `## How to Run` — canonical invocation (tool-framed)
5. `## Quick Reference` — flat command/endpoint list
6. `## Procedure` — numbered steps with exact commands
7. `## Pitfalls` — known limits, "looks broken but isn't"
8. `## Verification` — single command that proves it worked

### Tool framing (mandatory)

| WRONG | RIGHT |
|---|---|
| cat/head/tail | read_file |
| grep/rg/find/ls | search_files |
| sed/awk | patch |
| curl-to-scrape | web_extract |
| echo>file/heredoc | write_file |
| bash script.sh | terminal |

### Quality bar

- Exact commands from source. Never invent flags/paths/APIs.
- ~100 lines simple, ~200 complex.
- Larger scripts → `scripts/`, referenced by relative path.
- References → `references/`. Templates → `templates/`.

### Dynamic syntax (opt-in)

- `${HERMES_SKILL_DIR}` → absolute path of skill (default ON)
- `${HERMES_SESSION_ID}` → session ID if available
- `!`shell-cmd`` → runs at load, replaces stdout (default OFF)

## When done

Tell the user the skill name, category, and one-line summary. Suggest
trigger phrases for next-time use.
