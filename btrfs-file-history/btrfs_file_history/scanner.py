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
    exclusive_bytes: Optional[int] = None
    shared_bytes: Optional[int] = None
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
    """Fast checksum of the first *max_bytes* using BLAKE2b."""
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
    """lstat a file, returning None on error."""
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
    """Probe a single file inside one subvolume."""
    relative_path = relative_path.lstrip("/")
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
    """Scan a file across snapshots of the relevant subvolume family.

    Instead of scanning ALL subvolumes (which mixes unrelated ROOT
    snapshots with home snapshots, causing false created/deleted
    transitions), this function:

    1. Does a quick probe across all accessible subvolumes to find
       which ones contain the file.
    2. Identifies the "family" of related subvolumes (original +
       its snapshots) that contain the file.
    3. Performs detailed scanning only within that family.

    This ensures the timeline only shows transitions within the
    same subvolume lineage, producing correct modified/unchanged
    results instead of spurious created/deleted alternation.
    """
    relative_path = tree.normalize_file_path(relative_path)

    # Phase 1: Quick existence check across all subvolumes
    # to identify which family the file belongs to
    candidate_families: dict[str, list[Subvolume]] = {}
    # key = root ancestor uuid, value = family subvolumes that have the file

    for sv in tree.all_subvolumes:
        if subvol_filter:
            if not any(p in sv.path for p in subvol_filter):
                continue

        sv_base = tree.resolve_subvol_path(sv)
        if sv_base is None:
            continue

        test_path = sv_base / relative_path
        if _stat_file(test_path) is not None:
            # Find the root ancestor for this subvolume
            root_ancestor = _find_root_ancestor(sv, tree)
            root_uuid = root_ancestor.uuid
            if root_uuid not in candidate_families:
                candidate_families[root_uuid] = []
            candidate_families[root_uuid].append(sv)

    if not candidate_families:
        # File not found anywhere — return empty history
        return FileHistory(
            relative_path=relative_path,
            states=[],
            transitions=[],
        )

    # Phase 2: Pick the best family (the one with most hits)
    best_root_uuid = max(
        candidate_families,
        key=lambda k: len(candidate_families[k]),
    )

    # Get the full family (including subvolumes where file doesn't exist)
    best_root = tree.by_uuid[best_root_uuid]
    family = tree.get_family(best_root)

    # Apply user filter if specified
    if subvol_filter:
        family = [
            sv for sv in family
            if any(p in sv.path for p in subvol_filter)
        ]

    # Phase 3: Detailed probe of the family subvolumes only
    states: list[FileState] = []

    for sv in family:
        sv_base = tree.resolve_subvol_path(sv)
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


def _find_root_ancestor(
    sv: Subvolume, tree: SubvolumeTree,
) -> Subvolume:
    """Walk up the parent_uuid chain to find the root ancestor."""
    current = sv
    while (
        current.is_snapshot
        and current.parent_uuid
        and current.parent_uuid in tree.by_uuid
    ):
        current = tree.by_uuid[current.parent_uuid]
    return current


def _compute_transitions(
    states: list[FileState],
    checksum_available: bool,
) -> list[FileTransition]:
    """Determine change type between consecutive states.

    Skips all states before the file first appears.
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
    """Find physical extents shared across multiple file versions."""
    phys_map: dict[int, list[tuple[Subvolume, ExtentInfo]]] = {}

    for state in history.states:
        if not state.exists or not state.extents:
            continue
        for ext in state.extents:
            if "inline" in ext.flags.lower():
                continue
            if ext.physical_offset == 0 and ext.length == 0:
                continue
            phys_map.setdefault(ext.physical_offset, []).append(
                (state.subvolume, ext)
            )

    return {k: v for k, v in phys_map.items() if len(v) > 1}
