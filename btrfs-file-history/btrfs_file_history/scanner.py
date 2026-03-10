"""
Scan a file across all snapshots to determine creation, modification,
and deletion points.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .btrfs import Subvolume, ExtentInfo, filesystem_du, get_extents
from .tree import SubvolumeTree


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FileState:
    """State of a file within a specific snapshot."""

    subvolume: Subvolume
    exists: bool
    full_path: Optional[Path] = None
    size: Optional[int] = None
    mtime: Optional[float] = None
    inode: Optional[int] = None
    mode: Optional[int] = None
    checksum: Optional[str] = None
    exclusive_bytes: Optional[int] = None   # btrfs filesystem du
    shared_bytes: Optional[int] = None      # btrfs filesystem du
    extents: list[ExtentInfo] = field(default_factory=list)

    @property
    def is_dir(self) -> bool:
        return self.mode is not None and stat.S_ISDIR(self.mode)

    @property
    def is_regular(self) -> bool:
        return self.mode is not None and stat.S_ISREG(self.mode)


@dataclass
class FileTransition:
    """Transition of a file between two consecutive snapshots."""

    prev: Optional[FileState]
    curr: FileState
    change_type: str
    # "created" | "modified" | "unchanged" | "deleted" | "type_changed"


@dataclass
class FileHistory:
    """Complete history of a file across all scanned snapshots."""

    relative_path: str
    states: list[FileState]
    transitions: list[FileTransition]

    @property
    def created_in(self) -> Optional[Subvolume]:
        """The subvolume where the file first appeared."""
        for t in self.transitions:
            if t.change_type == "created":
                return t.curr.subvolume
        return None

    @property
    def modified_in(self) -> list[Subvolume]:
        """All subvolumes where the file was modified."""
        return [
            t.curr.subvolume
            for t in self.transitions
            if t.change_type == "modified"
        ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _partial_checksum(
    path: Path,
    max_bytes: int = 64 * 1024,
) -> Optional[str]:
    """Fast checksum of the first *max_bytes* using BLAKE2b.

    Uses ``O_NOFOLLOW`` to stay consistent with ``lstat()`` used
    elsewhere — never follows symlinks.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except (OSError, PermissionError):
        return None

    try:
        h = hashlib.blake2b(digest_size=16)
        data = os.read(fd, max_bytes)
        h.update(data)
        return h.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def _stat_file(path: Path) -> Optional[os.stat_result]:
    """``lstat`` a file, returning None on error."""
    try:
        return os.lstat(str(path))
    except (OSError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# probe_file — reusable single-snapshot probe
# ---------------------------------------------------------------------------

def probe_file(
    relative_path: str,
    subvolume: Subvolume,
    subvol_base: Path,
    *,
    compute_checksum: bool = False,
    compute_extents: bool = False,
    compute_du: bool = False,
) -> FileState:
    """Probe a single file inside one subvolume.

    Used by :func:`scan_file` for bulk scanning and directly by
    ``cmd_diff`` for targeted two-snapshot comparison.

    Args:
        relative_path: path relative to subvolume root
        subvolume: the Subvolume object
        subvol_base: resolved filesystem path to the subvolume root
        compute_checksum: compute BLAKE2b of the first 64 KiB
        compute_extents: collect extent map via filefrag
        compute_du: collect ``btrfs filesystem du`` data
    """
    full_path = subvol_base / relative_path

    st = _stat_file(full_path)
    if st is None:
        return FileState(subvolume=subvolume, exists=False)

    file_state = FileState(
        subvolume=subvolume,
        exists=True,
        full_path=full_path,
        size=st.st_size,
        mtime=st.st_mtime,
        inode=st.st_ino,
        mode=st.st_mode,
    )

    if compute_checksum and stat.S_ISREG(st.st_mode):
        file_state.checksum = _partial_checksum(full_path)

    if compute_extents and stat.S_ISREG(st.st_mode):
        file_state.extents = get_extents(full_path)

    if compute_du:
        du = filesystem_du(full_path)
        file_state.exclusive_bytes = du["exclusive"]
        file_state.shared_bytes = du["set_shared"]

    return file_state


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan_file(
    relative_path: str,
    tree: SubvolumeTree,
    mount_point: Path,
    *,
    compute_checksum: bool = False,
    compute_extents: bool = False,
    compute_du: bool = False,
    subvol_filter: Optional[list[str]] = None,
) -> FileHistory:
    """Scan a file in all accessible snapshots.

    Args:
        relative_path: path relative to subvolume root (e.g. ``etc/fstab``)
        tree: subvolume tree built from *mount_point*
        mount_point: btrfs mount point
        compute_checksum: compute BLAKE2b checksum for change detection
        compute_extents: collect extent map via filefrag
        compute_du: collect btrfs du (shared/exclusive bytes)
        subvol_filter: only scan subvolumes matching these path fragments
    """
    states: list[FileState] = []

    for sv in tree.all_subvolumes:
        if subvol_filter:
            if not any(p in sv.path for p in subvol_filter):
                continue

        sv_base = tree.resolve_subvol_path(sv, mount_point)
        if sv_base is None:
            continue

        states.append(probe_file(
            relative_path,
            sv,
            sv_base,
            compute_checksum=compute_checksum,
            compute_extents=compute_extents,
            compute_du=compute_du,
        ))

    transitions = _compute_transitions(states, compute_checksum)

    return FileHistory(
        relative_path=relative_path,
        states=states,
        transitions=transitions,
    )


def _compute_transitions(
    states: list[FileState],
    checksum_available: bool,
) -> list[FileTransition]:
    """Determine change type between consecutive states.

    Skips all states before the file first appears — a file absent
    in early snapshots is not marked as "deleted".
    """
    transitions: list[FileTransition] = []
    prev_state: Optional[FileState] = None
    file_seen = False

    for curr in states:
        if not file_seen:
            if not curr.exists:
                continue
            file_seen = True
            transitions.append(FileTransition(
                prev=None,
                curr=curr,
                change_type="created",
            ))
            prev_state = curr
            continue

        assert prev_state is not None

        if not prev_state.exists and curr.exists:
            change = "created"
        elif prev_state.exists and not curr.exists:
            change = "deleted"
        elif not prev_state.exists and not curr.exists:
            prev_state = curr
            continue
        else:
            change = _detect_modification(
                prev_state, curr, checksum_available,
            )

        transitions.append(FileTransition(
            prev=prev_state,
            curr=curr,
            change_type=change,
        ))
        prev_state = curr

    return transitions


def _detect_modification(
    old: FileState,
    new: FileState,
    checksum_available: bool,
) -> str:
    """Compare two existing file states to detect modifications."""
    # Type change detection (file ↔ directory)
    if old.mode is not None and new.mode is not None:
        if stat.S_ISDIR(old.mode) != stat.S_ISDIR(new.mode):
            return "type_changed"

    if old.size != new.size:
        return "modified"

    if old.mtime != new.mtime:
        if (
            checksum_available
            and old.checksum is not None
            and new.checksum is not None
        ):
            return (
                "unchanged" if old.checksum == new.checksum
                else "modified"
            )
        return "modified"

    if (
        checksum_available
        and old.checksum is not None
        and new.checksum is not None
        and old.checksum != new.checksum
    ):
        return "modified"

    return "unchanged"


# ---------------------------------------------------------------------------
# Shared extent analysis
# ---------------------------------------------------------------------------

def find_shared_extents(
    history: FileHistory,
) -> dict[int, list[tuple[Subvolume, ExtentInfo]]]:
    """Find physical extents shared across multiple file versions.

    Filters out inline extents (stored in metadata, not data blocks)
    which would produce false sharing results.
    """
    phys_map: dict[int, list[tuple[Subvolume, ExtentInfo]]] = {}

    for state in history.states:
        if not state.exists or not state.extents:
            continue
        for ext in state.extents:
            # Skip inline extents — physical_offset is meaningless
            if "inline" in ext.flags.lower():
                continue
            if ext.physical_offset == 0 and ext.length == 0:
                continue

            phys_map.setdefault(ext.physical_offset, []).append(
                (state.subvolume, ext)
            )

    return {k: v for k, v in phys_map.items() if len(v) > 1}
