"""SKILL.md schema validation. Enforces HARD constraints from Hermes spec.

Hard rules (HARDLINE):
  - frontmatter must have name + description
  - description ≤ 60 characters (HARD: system truncates; longer = never routes)
  - name matches ^[a-z0-9][a-z0-9._-]*$ and ≤ 64 chars
  - author must equal FORCE_AUTHOR (privacy: never use environment identity)
  - body must have content after frontmatter
  - content ≤ 100,000 characters
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from . import yaml_mini

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 60
MAX_CONTENT_CHARS = 100_000
MAX_FILE_BYTES = 1_048_576

VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
FORCE_AUTHOR = "hermes-skill-system"

ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}


class ValidationError(ValueError):
    pass


def validate_name(name: str) -> Optional[str]:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            f"hyphens, dots, and underscores. Must start with a letter or digit."
        )
    return None


def validate_file_path(file_path: str) -> Optional[str]:
    if not file_path:
        return "file_path is required."
    if ".." in file_path:
        return "Path traversal ('..') is not allowed."
    parts = Path(file_path).parts
    if not parts:
        return "file_path must contain at least one component."
    if parts[0] not in ALLOWED_SUBDIRS:
        allowed = ", ".join(sorted(ALLOWED_SUBDIRS))
        return f"File must be under one of: {allowed}. Got: '{file_path}'"
    if len(parts) < 2:
        return f"Provide a file path, not just a directory. Example: '{parts[0]}/example.md'"
    return None


def validate_content(content: str, label: str = "SKILL.md") -> Optional[str]:
    if not content.strip():
        return f"{label}: content cannot be empty."
    if len(content) > MAX_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_CONTENT_CHARS:,}). Consider splitting into a smaller "
            f"SKILL.md with supporting files in references/ or templates/."
        )
    return None


def validate_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse + validate frontmatter. Returns (parsed, body).

    Raises ValidationError on any structural problem.
    """
    if not content.startswith("---"):
        raise ValidationError(
            "SKILL.md must start with YAML frontmatter (---). "
            "See existing skills for format."
        )
    m = yaml_mini._FRONTMATTER_RE.match(content)
    if not m:
        raise ValidationError("SKILL.md frontmatter is not closed. Ensure you have a closing '---' line.")
    try:
        parsed = yaml_mini.parse(m.group(1))
    except yaml_mini.FrontmatterError as e:
        raise ValidationError(f"YAML frontmatter parse error: {e}") from e
    if not isinstance(parsed, dict):
        raise ValidationError("Frontmatter must be a YAML mapping (key: value pairs).")
    if "name" not in parsed:
        raise ValidationError("Frontmatter must include 'name' field.")
    if "description" not in parsed:
        raise ValidationError("Frontmatter must include 'description' field.")
    if "version" not in parsed:
        raise ValidationError("Frontmatter must include 'version' field (e.g. 0.1.0).")
    if "author" not in parsed:
        raise ValidationError(
            f"Frontmatter must include 'author' field. Use the literal "
            f"value '{FORCE_AUTHOR}' (privacy: never use environment identity)."
        )
    if parsed["author"] != FORCE_AUTHOR:
        raise ValidationError(
            f"author must be '{FORCE_AUTHOR}' (got '{parsed['author']}'). "
            f"Privacy: never write environment-derived identity into skills "
            f"that may be shared or published."
        )
    desc = str(parsed["description"])
    if len(desc) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(
            f"Frontmatter description is {len(desc)} characters "
            f"(limit: {MAX_DESCRIPTION_LENGTH}). "
            f"Anything past 60 chars is silently cut by the system-prompt "
            f"skill index and never routes. Cut it down before saving."
        )
    name_err = validate_name(str(parsed["name"]))
    if name_err:
        raise ValidationError(f"Invalid name in frontmatter: {name_err}")
    body = content[m.end():].strip()
    if not body:
        raise ValidationError(
            "SKILL.md must have content after the frontmatter "
            "(instructions, procedures, etc.)."
        )
    return parsed, body


def parse(content: str) -> dict[str, Any]:
    """Just parse the frontmatter without validating. Use validate_frontmatter for strict checks."""
    m = yaml_mini._FRONTMATTER_RE.match(content)
    if not m:
        return {}
    return yaml_mini.parse(m.group(1))
