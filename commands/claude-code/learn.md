---
description: Author a reusable skill from described sources (files, URLs, conversation, or pasted text). Triggers the standards-guided SKILL.md creation flow.
argument-hint: <workflow description, paths, URLs, or "what we just did">
---

# /learn — Skill Authoring

The user wants a reusable skill saved. Resolve what they described using
the tools you have:
- `read_file` / `search_files` for local files or directories
- `web_extract` for URLs
- The current conversation history if they referred to something you just did
- Pasted text as-is

Then author ONE SKILL.md via `skill_manage` (action="create"). Do NOT call
`skill_manage` directly here — the user runs `/learn` precisely so you
follow the standards below.

## Authoring Standards (HARDLINE)

These are the same rules a maintainer enforces in review. Violations fail
silently in production (truncated description never routes; bad author
leaks identity on shared skills).

### Frontmatter (REQUIRED)

```yaml
---
name: <slug>                    # kebab-case, ≤64 chars, ^[a-z0-9][a-z0-9._-]*$
description: <one sentence>     # HARD: ≤60 characters (HARDLINE — see below)
version: 0.1.0                 # semver
author: hermes-skill-system     # literal value, NEVER environment identity
platforms: [macos, linux]       # optional, only if OS-bound primitive
metadata:
  hermes:
    tags: [Tag1, Tag2]
---
```

**description ≤ 60 characters** — the system-prompt skill index truncates
at 60 chars and loads it every session. Anything past char 60 is silently
cut and never routes. After writing the description, COUNT the characters;
if over 60, cut it down before saving. Do not ship and hope.

```
Good (42):  Search arXiv papers by keyword, author, or ID.
Bad (123):  A comprehensive skill that lets the agent search arXiv
            for academic papers using keywords, authors, and categories.
```

**author = `hermes-skill-system` literally.** Never `os.getlogin()`,
`git config user.name`, or anything you can probe. Skills get shared and
published; an environment-derived name is a privacy leak the user never
opted into.

**platforms** only when the skill uses an OS-bound primitive (`osascript`,
`apt`, `systemctl`, `/proc`, `os.setsid`, `fcntl`, `termios`, `winreg`).
Prefer cross-platform (`tempfile.gettempdir()`, `pathlib.Path`, `psutil`).
Omit the field for portable skills.

### Body (8 sections, omit only if no content)

1. `# <Human Title>` + 2-3 sentence intro
   What it does, what it doesn't do, key dep (e.g. "stdlib only")
2. `## When to Use`
   Bullet list of concrete trigger phrases the user would say
3. `## Prerequisites`
   Exact env vars, install steps, credentials needed
4. `## How to Run`
   Canonical invocation, framed through Hermes tools (`read_file`,
   `search_files`, `patch`, `web_extract`, `write_file`, `terminal`)
5. `## Quick Reference`
   Flat command/endpoint list, no narration
6. `## Procedure`
   Numbered steps with copy-paste-exact commands
7. `## Pitfalls`
   Known limits, rate limits, things that look broken but aren't
8. `## Verification`
   One command/check that proves the skill worked

### Tool framing (mandatory)

Always frame commands through the wrapped tools, never raw shell:

| WRONG | RIGHT |
|---|---|
| `cat file.txt` | `read_file` |
| `grep -r "x" .` | `search_files` |
| `sed -i 's/x/y/'` | `patch` |
| `curl url \| grep` | `web_extract` |
| `echo "x" > file` | `write_file` |
| `bash script.sh` | `terminal` (with `cwd` to the script) |

Third-party CLIs (ffmpeg, gh, SDK scripts) are fine inside `scripts/` files
referenced by relative path. The prose still names the wrapped tool.

### Quality bar

- Exact commands, endpoint URLs, function signatures from the source. NEVER
  invent flags, paths, or APIs. If you didn't see it in the source, don't
  write it.
- ~100 lines for simple, ~200 for complex. Don't re-paste source docs.
- Don't write a router/index/hub skill that only points at other skills.
- Larger scripts → `scripts/<name>.<ext>`, referenced by relative path.
- References → `references/`. Templates → `templates/`. Static assets → `assets/`.

### Dynamic syntax (optional, opt-in)

- `${HERMES_SKILL_DIR}` → replaced with the skill's absolute path at load.
- `${HERMES_SESSION_ID}` → session ID if available.
- `!`shell-cmd`` → runs the command at load, replaces with stdout. **Off
  by default** for security. Must be enabled via `SKILL_INLINE_SHELL=1`.

Unresolved tokens are left in place so the author can debug.

## When done

Tell the user the skill name, its category, and a one-line summary of
what it captured. Suggest the right trigger phrases they should use next
time to invoke it.
