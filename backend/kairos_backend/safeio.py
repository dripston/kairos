"""Crash- and Ctrl-C-safe file output for scripts and reports.

atomic_write_text() writes to a temp file in the same folder, fsyncs, then renames over the target, so a
reader (or an interrupted run) sees either the old file or the new one, never half of one.
interruptible() runs a script's main(); Ctrl-C prints a one-line partial summary and returns 130.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Union

EXIT_INTERRUPTED = 130


def atomic_write_text(path: Union[str, Path], text: str, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def interruptible(main: Callable[[], Optional[int]], partial: Optional[Callable[[], str]] = None) -> int:
    """Run main(); on Ctrl-C print what finished (partial()) and return 130 (128 + SIGINT)."""
    t0 = time.monotonic()
    try:
        return main() or 0
    except KeyboardInterrupt:
        extra = ""
        if partial is not None:
            try:
                extra = " " + partial()
            except Exception:
                extra = ""
        print(f"\ninterrupted after {time.monotonic() - t0:.1f}s.{extra} No report file was half-written: "
              f"earlier reports are unchanged (writes are temp file + rename).", file=sys.stderr)
        return EXIT_INTERRUPTED
