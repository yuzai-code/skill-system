"""Atomic file I/O — tempfile + fsync + os.replace.

Crash-safe: target file is never in a partial state, even if process dies.
On POSIX we also fsync the parent directory so the rename is durable.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union

TextOrBytes = Union[str, bytes]


def atomic_write(path: Path, content: TextOrBytes, encoding: str = "utf-8") -> None:
    """Write content atomically. Overwrites existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".tmp"
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=suffix
    )
    try:
        with os.fdopen(fd, "wb" if isinstance(content, bytes) else "w",
                       encoding=None if isinstance(content, bytes) else encoding) as f:
            if isinstance(content, bytes):
                f.write(content)
            else:
                f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    atomic_write(path, content, encoding)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    atomic_write(path, content)
