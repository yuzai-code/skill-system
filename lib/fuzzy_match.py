"""Fuzzy find-and-replace — whitespace/indent tolerant.

Hermes-style: handles whitespace normalization, indentation differences,
escape sequences, and block-anchor matching. Saves the agent from exact-match
failures on minor formatting mismatches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FuzzyResult:
    new_content: str
    match_count: int
    strategy: str
    error: Optional[str] = None


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(s: str) -> str:
    """Collapse all whitespace runs to single spaces; strip leading/trailing."""
    return _WHITESPACE_RE.sub(" ", s).strip()


def fuzzy_find_and_replace(
    content: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> FuzzyResult:
    """Try multiple strategies in order: exact, line-trimmed, whitespace-normalized.

    Returns FuzzyResult. On any match, new_content contains the replacement.
    """
    if not old:
        return FuzzyResult(content, 0, "none", "old_string is required for patch.")
    if new is None:
        return FuzzyResult(content, 0, "none", "new_string is required for patch.")

    if old in content:
        return _apply_exact(content, old, new, replace_all)

    r = _try_line_trim(content, old, new, replace_all)
    if r is not None:
        return r

    r2 = _try_normalized(content, old, new, replace_all)
    if r2 is not None:
        return r2

    return FuzzyResult(
        content,
        0,
        "none",
        f"old_string not found in target file. Tried exact, line-trimmed, "
        f"and whitespace-normalized matching. The first 80 chars of old_string: "
        f"{old[:80]!r}",
    )


def _apply_exact(
    content: str, old: str, new: str, replace_all: bool
) -> FuzzyResult:
    count = content.count(old)
    if count > 1 and not replace_all:
        return FuzzyResult(
            content,
            count,
            "exact",
            f"Found {count} matches for old_string; pass replace_all=true or "
            f"include more surrounding context to disambiguate.",
        )
    if count == 0:
        return FuzzyResult(content, 0, "exact", "no match")
    new_content = content.replace(old, new, -1 if replace_all else 1)
    actual = count if replace_all else min(count, 1)
    return FuzzyResult(new_content, actual, "exact")


def _try_line_trim(
    content: str, old: str, new: str, replace_all: bool
) -> Optional[FuzzyResult]:
    """Match line-by-line with strip() tolerance on both leading and trailing whitespace."""
    old_lines = old.split("\n")
    content_lines = content.split("\n")
    old_stripped = [line.strip() for line in old_lines]
    new_stripped = [line.strip() for line in new.split("\n")]
    if not all(old_stripped):
        return None

    first_stripped = old_stripped[0]
    occurrences = []
    for i, line in enumerate(content_lines):
        if line.strip() == first_stripped:
            if i + len(old_stripped) > len(content_lines):
                continue
            window = [content_lines[i + j].strip() for j in range(len(old_stripped))]
            if window == old_stripped:
                occurrences.append(i)

    if not occurrences:
        return None
    if len(occurrences) > 1 and not replace_all:
        return FuzzyResult(
            content,
            len(occurrences),
            "line-trim",
            f"Found {len(occurrences)} line-trimmed matches; "
            f"pass replace_all=true or include more context.",
        )
    out_lines = list(content_lines)
    insert_pos = occurrences[0] + len(old_stripped)
    out_lines[occurrences[0] : insert_pos] = new_stripped
    return FuzzyResult(
        "\n".join(out_lines),
        len(occurrences),
        "line-trim",
    )


def _try_normalized(
    content: str, old: str, new: str, replace_all: bool
) -> Optional[FuzzyResult]:
    """Whitespace-normalized substring match. Last resort, lossy on indentation."""
    norm_old = _normalize_ws(old)
    if not norm_old:
        return None
    norm_content = _normalize_ws(content)
    idx = norm_content.find(norm_old)
    if idx < 0:
        return None
    if not replace_all and norm_content.find(norm_old, idx + 1) >= 0:
        return FuzzyResult(
            content,
            2,
            "whitespace-normalized",
            "Found 2+ normalized matches; pass replace_all=true or include more context.",
        )
    return FuzzyResult(
        content.replace(_denormalize_first(content, norm_old, idx), new, 1),
        1,
        "whitespace-normalized",
    )


def _denormalize_first(content: str, norm_old: str, norm_idx: int) -> str:
    """Find the raw substring in content that corresponds to norm_old at norm_idx.

    Linear scan: for each position, normalize a window of same length as norm_old
    and check equality. O(n * window) but only one pass is needed.
    """
    words = norm_old.split(" ")
    if not words:
        return norm_old
    i = 0
    while i < len(content):
        j = i
        word_idx = 0
        while j < len(content) and word_idx < len(words):
            if content[j].isspace():
                j += 1
                continue
            k = j
            while k < len(content) and not content[k].isspace():
                k += 1
            token = content[j:k]
            if token != words[word_idx]:
                break
            word_idx += 1
            j = k
        if word_idx == len(words):
            return content[i:j]
        if i < len(content) and content[i].isspace():
            i += 1
        else:
            i = j if j > i else i + 1
    return norm_old
