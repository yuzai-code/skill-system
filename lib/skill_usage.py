"""Usage telemetry sidecar — ~/.{cli}/skills/.usage.json

Tracks per-skill activity for the curator state machine.
Sidecar (not frontmatter) for two reasons:
  1. Avoids conflict with user-authored SKILL.md content
  2. Works for bundled/hub skills whose frontmatter is read-only

Concurrency: fcntl file lock (POSIX). Best-effort: telemetry failures
never break the underlying tool call.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_record() -> dict[str, Any]:
    return {
        "created_by": None,
        "use_count": 0,
        "view_count": 0,
        "patch_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "last_patched_at": None,
        "created_at": _now_iso(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
        "absorbed_into": None,
    }


class UsageStore:
    """Sidecar JSON store with fcntl locking."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.usage_file = skills_dir / ".usage.json"
        self.lock_file = skills_dir / ".usage.json.lock"

    @contextmanager
    def _lock(self) -> Iterator[int]:
        """Acquire exclusive fcntl lock on sidecar."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.usage_file.exists():
            return {}
        try:
            data = json.loads(self.usage_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to read %s: %s", self.usage_file, e)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def save(self, data: dict[str, dict[str, Any]]) -> None:
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.usage_file.parent), prefix=".usage_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.usage_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @contextmanager
    def transaction(self) -> Iterator[dict[str, dict[str, Any]]]:
        """Read-modify-write under lock. Yields the full data dict (mutate in place)."""
        with self._lock() as _fd:
            data = self.load()
            try:
                yield data
            except BaseException:
                raise
            else:
                self.save(data)

    def get(self, name: str) -> dict[str, Any]:
        with self.transaction() as data:
            rec = data.get(name)
            if not isinstance(rec, dict):
                rec = dict(_empty_record())
            else:
                for k, v in _empty_record().items():
                    rec.setdefault(k, v)
            return rec

    def bump(self, name: str, *, kind: str) -> None:
        """Bump a counter: kind in {'use', 'view', 'patch'}.

        Always tracks (telemetry is observability for all skills, not just
        curator-managed). Failures log and return — never break the caller.
        """
        try:
            with self.transaction() as data:
                rec = data.get(name)
                if not isinstance(rec, dict):
                    rec = dict(_empty_record())
                count_key = f"{kind}_count"
                ts_key = f"last_{kind}ed_at" if kind != "view" else "last_viewed_at"
                if kind == "use":
                    ts_key = "last_used_at"
                elif kind == "patch":
                    ts_key = "last_patched_at"
                rec[count_key] = int(rec.get(count_key) or 0) + 1
                rec[ts_key] = _now_iso()
                data[name] = rec
        except Exception as e:
            logger.debug("bump(%s, %s) failed: %s", name, kind, e)

    def set_state(self, name: str, state: str) -> None:
        if state not in _VALID_STATES:
            return
        try:
            with self.transaction() as data:
                rec = data.get(name)
                if not isinstance(rec, dict):
                    rec = dict(_empty_record())
                rec["state"] = state
                if state == STATE_ARCHIVED:
                    rec["archived_at"] = _now_iso()
                elif state == STATE_ACTIVE:
                    rec["archived_at"] = None
                data[name] = rec
        except Exception as e:
            logger.debug("set_state(%s, %s) failed: %s", name, state, e)

    def set_pinned(self, name: str, pinned: bool) -> None:
        try:
            with self.transaction() as data:
                rec = data.get(name)
                if not isinstance(rec, dict):
                    rec = dict(_empty_record())
                rec["pinned"] = bool(pinned)
                data[name] = rec
        except Exception as e:
            logger.debug("set_pinned(%s, %s) failed: %s", name, pinned, e)

    def set_absorbed_into(self, name: str, target: str | None) -> None:
        try:
            with self.transaction() as data:
                rec = data.get(name)
                if not isinstance(rec, dict):
                    rec = dict(_empty_record())
                rec["absorbed_into"] = target if target else None
                data[name] = rec
        except Exception as e:
            logger.debug("set_absorbed_into(%s, %s) failed: %s", name, target, e)

    def forget(self, name: str) -> None:
        try:
            with self.transaction() as data:
                data.pop(name, None)
        except Exception as e:
            logger.debug("forget(%s) failed: %s", name, e)

    def latest_activity_at(self, record: dict[str, Any]) -> str | None:
        latest_dt = None
        latest_raw = None
        for key in ("last_used_at", "last_viewed_at", "last_patched_at"):
            raw = record.get(key)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
                latest_raw = str(raw)
        return latest_raw
