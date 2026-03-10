"""
Detailed comparison of a file between two snapshots.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Optional

from .scanner import FileState


@dataclass
class FileDiff:
    """Result of comparing two file versions."""

    old: FileState
    new: FileState
    size_delta: int
    mtime_delta: float
    content_identical: bool
    text_diff: Optional[str]
    shared_extent_count: int
    old_extent_count: int
    new_extent_count: int


def diff_states(
    old: FileState,
    new: FileState,
    *,
    text_diff: bool = False,
    max_diff_size: int = 1024 * 1024,
) -> FileDiff:
    """Compare two file states in detail.

    Args:
        old: file state in the older snapshot
        new: file state in the newer snapshot
        text_diff: whether to generate a unified text diff
        max_diff_size: skip text diff for files larger than this
    """
    size_delta = (new.size or 0) - (old.size or 0)
    mtime_delta = (new.mtime or 0.0) - (old.mtime or 0.0)

    content_identical = (
        old.checksum is not None
        and new.checksum is not None
        and old.checksum == new.checksum
    )

    old_phys = {e.physical_offset for e in old.extents}
    new_phys = {e.physical_offset for e in new.extents}
    shared = old_phys & new_phys

    udiff: Optional[str] = None
    if text_diff and old.full_path and new.full_path:
        udiff = _generate_text_diff(old, new, max_diff_size)

    return FileDiff(
        old=old,
        new=new,
        size_delta=size_delta,
        mtime_delta=mtime_delta,
        content_identical=content_identical,
        text_diff=udiff,
        shared_extent_count=len(shared),
        old_extent_count=len(old_phys),
        new_extent_count=len(new_phys),
    )


def _generate_text_diff(
    old: FileState,
    new: FileState,
    max_size: int,
) -> Optional[str]:
    """Generate unified diff between two file versions.

    Returns None if either file is too large or unreadable.
    """
    assert old.full_path is not None
    assert new.full_path is not None

    if (old.size or 0) > max_size or (new.size or 0) > max_size:
        return None

    try:
        old_lines = old.full_path.read_text(
            errors="replace",
        ).splitlines(keepends=True)
        new_lines = new.full_path.read_text(
            errors="replace",
        ).splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None

    result = "".join(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{old.subvolume.path}/{old.full_path.name}",
        tofile=f"{new.subvolume.path}/{new.full_path.name}",
    ))
    return result or None
