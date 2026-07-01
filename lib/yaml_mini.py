"""Minimal YAML frontmatter parser — no PyYAML dependency.

Supports the subset we need for SKILL.md:
  - key: value
  - key: [item1, item2, ...]
  - key:
      - item1
      - item2
  - nested: key (2 levels)
  - quoted values: "..." or '...'
  - comments (# ...)

Strict subset: refuses anything ambiguous. Use only for frontmatter
extraction, not arbitrary YAML.
"""

from __future__ import annotations

import re
from typing import Any


class FrontmatterError(ValueError):
    pass


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def extract(content: str) -> dict[str, Any]:
    """Return frontmatter dict. Empty dict if no frontmatter."""
    if not content:
        return {}
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    return parse(m.group(1))


def parse(yaml_text: str) -> dict[str, Any]:
    """Parse a minimal YAML subset. Raises FrontmatterError on malformed input."""
    lines = _strip_comments(yaml_text).split("\n")
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2 != 0:
            raise FrontmatterError(f"line {lineno}: odd indent {indent}")
        if "\t" in raw:
            raise FrontmatterError(f"line {lineno}: tab in indentation")
        if raw.lstrip().startswith("- "):
            _parse_list_item(raw, indent, lineno, stack, root)
            continue
        _parse_kv(raw, indent, lineno, stack, root)

    return root


def _strip_comments(text: str) -> str:
    """Strip trailing # comments but preserve # inside quoted strings.

    A line that starts with # (after optional whitespace) is fully removed.
    A # after a non-quoted value starts a comment.
    """
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            out.append("")
            continue
        in_single = in_double = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                cut = i
                break
        out.append(line[:cut].rstrip())
    return "\n".join(out)


def _parse_kv(
    raw: str,
    indent: int,
    lineno: int,
    stack: list[tuple[int, dict[str, Any]]],
    root: dict[str, Any],
) -> None:
    line = raw.lstrip(" ")
    if ":" not in line:
        raise FrontmatterError(f"line {lineno}: expected key:value, got {raw!r}")
    key, _, value = line.partition(":")
    key = key.strip()
    value = value.strip()
    parent = _parent_for(indent, stack, root)
    if value == "":
        parent[key] = {}
        stack.append((indent, parent[key]))
    else:
        parent[key] = _parse_scalar(value, lineno)


def _parse_list_item(
    raw: str,
    indent: int,
    lineno: int,
    stack: list[tuple[int, dict[str, Any]]],
    root: dict[str, Any],
) -> None:
    parent = _parent_for(indent, stack, root)
    if not stack or stack[-1][0] >= indent:
        raise FrontmatterError(
            f"line {lineno}: list item without parent key (indent={indent})"
        )
    list_parent = stack[-1][1]
    key_of_list = _find_last_key_at(stack, indent)
    if key_of_list is None:
        raise FrontmatterError(f"line {lineno}: orphan list item")
    if key_of_list not in list_parent or not isinstance(list_parent[key_of_list], list):
        list_parent[key_of_list] = []
    item = raw.strip()[2:].strip()
    list_parent[key_of_list].append(_parse_scalar(item, lineno))


def _parent_for(
    indent: int,
    stack: list[tuple[int, dict[str, Any]]],
    root: dict[str, Any],
) -> dict[str, Any]:
    while stack and stack[-1][0] >= indent:
        stack.pop()
    if not stack:
        stack.append((-1, root))
    return stack[-1][1]


def _find_last_key_at(
    stack: list[tuple[int, dict[str, Any]]], indent: int
) -> str | None:
    if len(stack) < 2:
        return None
    child_dict = stack[-1][1]
    parent_dict = stack[-2][1]
    for k, v in parent_dict.items():
        if v is child_dict:
            return k
    return None


def _parse_scalar(value: str, lineno: int) -> Any:
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p.strip(), lineno) for p in _split_list(inner)]
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if value.lower() in ("null", "~"):
        return None
    if _is_int(value):
        return int(value)
    if _is_float(value):
        return float(value)
    return value


def _split_list(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    in_quote: str | None = None
    for ch in inner:
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in '"\'':
            in_quote = ch
            cur.append(ch)
            continue
        if ch in "[{":
            depth += 1
            cur.append(ch)
            continue
        if ch in "]}":
            depth -= 1
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _is_int(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", s))


def _is_float(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+\.\d+", s))


def dump(data: dict[str, Any]) -> str:
    """Serialize back to frontmatter format. Best-effort."""
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for k2, v2 in v.items():
                if isinstance(v2, list):
                    lines.append(f"  {k2}: [{', '.join(str(x) for x in v2)}]")
                else:
                    lines.append(f"  {k2}: {_fmt_scalar(v2)}")
        elif isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {_fmt_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    s = str(v)
    if any(c in s for c in [":", "#", '"', "'"]) or s == "":
        return f'"{s}"'
    return s
