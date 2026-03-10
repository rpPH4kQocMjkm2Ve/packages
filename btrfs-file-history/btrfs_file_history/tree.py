"""
Build a tree structure from flat subvolume list using parent_uuid linkage.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional

from .btrfs import Subvolume, list_subvolumes, get_fs_root, find_mount_point


@dataclass
class SubvolumeTree:
    """Hierarchical tree of btrfs subvolumes.

    Roots are subvolumes whose parent_uuid is absent or points to a
    subvolume not present in the listing (typically the top-level id=5
    which ``btrfs subvolume list`` does not include).
    """

    roots: list[Subvolume]
    by_uuid: dict[str, Subvolume]
    by_id: dict[int, Subvolume]
    all_subvolumes: list[Subvolume]
    fs_root: str          # FSROOT of the mount point (empty for top-level)
    actual_mount: Path    # resolved btrfs mount point from findmnt
    user_mount: Path      # the path the user actually passed in

    # Maps subvol_id → resolved filesystem path (found by scanning)
    _resolved_paths: dict[int, Path]

    @classmethod
    def build(cls, mount_point: str) -> SubvolumeTree:
        """Build tree from a mounted btrfs filesystem."""
        subvols = list_subvolumes(mount_point)

        try:
            actual_mount = find_mount_point(mount_point)
        except RuntimeError:
            actual_mount = Path(mount_point)

        try:
            fs_root = get_fs_root(mount_point)
        except RuntimeError:
            fs_root = ""

        by_uuid: dict[str, Subvolume] = {}
        by_id: dict[int, Subvolume] = {}

        for sv in subvols:
            if sv.uuid:
                by_uuid[sv.uuid] = sv
            by_id[sv.subvol_id] = sv

        for sv in subvols:
            sv.children = []

        roots: list[Subvolume] = []
        for sv in subvols:
            if sv.parent_uuid and sv.parent_uuid in by_uuid:
                parent = by_uuid[sv.parent_uuid]
                parent.children.append(sv)
            else:
                roots.append(sv)

        for sv in subvols:
            sv.children.sort(key=lambda c: c.ogen)

        user_mount = Path(mount_point)

        tree = cls(
            roots=roots,
            by_uuid=by_uuid,
            by_id=by_id,
            all_subvolumes=subvols,
            fs_root=fs_root,
            actual_mount=actual_mount,
            user_mount=user_mount,
            _resolved_paths={},
        )

        tree._scan_subvol_paths()
        return tree

    # ------------------------------------------------------------------
    # Subvolume filesystem path discovery
    # ------------------------------------------------------------------

    def _scan_subvol_paths(self) -> None:
        """Discover actual filesystem paths for all subvolumes."""
        needed: set[int] = {sv.subvol_id for sv in self.all_subvolumes}

        for sv in self.all_subvolumes:
            candidate = self._compute_path_candidate(sv)
            if candidate is not None and candidate.is_dir():
                self._resolved_paths[sv.subvol_id] = candidate

        still_needed = needed - set(self._resolved_paths.keys())
        if still_needed:
            self._scan_snapshot_dirs(still_needed)

    def _compute_path_candidate(self, sv: Subvolume) -> Optional[Path]:
        """Compute expected filesystem path from btrfs path."""
        sv_path = sv.path

        if self.fs_root:
            if sv_path == self.fs_root:
                return self.actual_mount
            if sv_path.startswith(self.fs_root + "/"):
                relative = sv_path[len(self.fs_root) + 1:]
                return self.actual_mount / relative
            return None

        return self.actual_mount / sv_path

    def _scan_snapshot_dirs(self, needed: set[int]) -> None:
        """Walk common snapshot directories to find subvolumes."""
        by_name: dict[str, list[Subvolume]] = {}
        for sv in self.all_subvolumes:
            if sv.subvol_id in needed:
                by_name.setdefault(sv.name, []).append(sv)

        if not by_name:
            return

        scan_roots: list[Path] = []
        for dirname in ("snapshots", ".snapshots", "snap", "btrbk"):
            candidate = self.actual_mount / dirname
            if candidate.is_dir():
                scan_roots.append(candidate)

        if self.user_mount != self.actual_mount:
            if self.user_mount.is_dir():
                scan_roots.append(self.user_mount)

        for scan_root in scan_roots:
            self._walk_for_subvols(scan_root, by_name, needed, depth=0)

    def _walk_for_subvols(
        self,
        directory: Path,
        by_name: dict[str, list[Subvolume]],
        needed: set[int],
        depth: int,
    ) -> None:
        """Recursively walk directory looking for subvolume roots."""
        if depth > 4 or not needed:
            return

        try:
            entries = sorted(directory.iterdir())
        except (OSError, PermissionError):
            return

        for entry in entries:
            if not entry.is_dir():
                continue

            name = entry.name
            if name in by_name:
                for sv in list(by_name[name]):
                    if sv.subvol_id not in needed:
                        continue
                    if self._is_subvol_root(entry, sv.subvol_id):
                        self._resolved_paths[sv.subvol_id] = entry
                        needed.discard(sv.subvol_id)

            if needed:
                self._walk_for_subvols(
                    entry, by_name, needed, depth + 1,
                )

    def _is_subvol_root(
        self, path: Path, expected_id: Optional[int] = None,
    ) -> bool:
        """Check if path is a btrfs subvolume root."""
        if expected_id is not None:
            try:
                result = subprocess.run(
                    ["btrfs", "subvolume", "show", str(path)],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("Subvolume ID:"):
                            found_id = int(
                                stripped.split(":", 1)[1].strip()
                            )
                            return found_id == expected_id
            except (OSError, ValueError):
                pass

        try:
            st = os.lstat(str(path))
            return st.st_ino == 256
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Tree traversal
    # ------------------------------------------------------------------

    def iter_depth_first(
        self,
    ) -> Iterator[tuple[int, Subvolume, bool]]:
        """Yield (depth, subvolume, is_last_child) depth-first."""
        def _walk(
            sv: Subvolume, depth: int, is_last: bool,
        ) -> Iterator[tuple[int, Subvolume, bool]]:
            yield (depth, sv, is_last)
            for i, child in enumerate(sv.children):
                yield from _walk(
                    child, depth + 1, i == len(sv.children) - 1,
                )

        for i, root in enumerate(self.roots):
            yield from _walk(root, 0, i == len(self.roots) - 1)

    def get_lineage(self, sv: Subvolume) -> list[Subvolume]:
        """Return the chain from root to sv (inclusive)."""
        chain: list[Subvolume] = [sv]
        current = sv
        while current.parent_uuid and current.parent_uuid in self.by_uuid:
            current = self.by_uuid[current.parent_uuid]
            chain.append(current)
        chain.reverse()
        return chain

    def find_by_path(self, path_fragment: str) -> list[Subvolume]:
        """Search subvolumes by path substring."""
        return [
            sv for sv in self.all_subvolumes
            if path_fragment in sv.path
        ]

    def get_family(self, sv: Subvolume) -> list[Subvolume]:
        """Get a subvolume and all its snapshots (children by parent_uuid).

        If sv is a snapshot itself, finds the original parent first,
        then returns the parent and all its snapshots.

        Returns subvolumes sorted by ogen.
        """
        root = sv
        while root.is_snapshot and root.parent_uuid in self.by_uuid:
            root = self.by_uuid[root.parent_uuid]

        family: set[str] = set()
        self._collect_descendants(root, family)

        result = [
            s for s in self.all_subvolumes
            if s.uuid in family
        ]
        result.sort(key=lambda s: s.ogen)
        return result

    def _collect_descendants(
        self, sv: Subvolume, uuids: set[str],
    ) -> None:
        """Recursively collect sv and all its snapshot descendants."""
        uuids.add(sv.uuid)
        for child in sv.children:
            self._collect_descendants(child, uuids)

    # ------------------------------------------------------------------
    # Subvolume path resolution
    # ------------------------------------------------------------------

    def resolve_subvol_path(
        self,
        sv: Subvolume,
        mount_point: Optional[Path] = None,
    ) -> Optional[Path]:
        """Resolve actual filesystem path for a subvolume."""
        if mount_point is not None:
            return self._resolve_with_base(sv, mount_point)

        if sv.subvol_id in self._resolved_paths:
            return self._resolved_paths[sv.subvol_id]

        return self._compute_path_candidate(sv)

    def _resolve_with_base(
        self, sv: Subvolume, base: Path,
    ) -> Optional[Path]:
        """Resolve subvolume path using explicit base."""
        sv_path = sv.path
        if self.fs_root:
            if sv_path == self.fs_root:
                return base
            if sv_path.startswith(self.fs_root + "/"):
                relative = sv_path[len(self.fs_root) + 1:]
                return base / relative
            return None
        return base / sv_path

    # ------------------------------------------------------------------
    # File path normalization
    # ------------------------------------------------------------------

    def normalize_file_path(self, file_path: str) -> str:
        """Normalize user-supplied file path to subvolume-relative.

        Handles three cases:

        1. **Absolute path** (``/home/user/file.txt``):
           Uses findmnt to find mount point, computes path relative
           to subvolume root.

        2. **Relative path** (``file.txt``, ``subdir/file.txt``):
           Resolved against CWD first to produce an absolute path,
           then handled as case 1.  This ensures that running the
           tool from ``/home/user`` with argument ``file.txt``
           correctly produces ``user/file.txt`` (relative to the
           ``home`` subvolume root), not just ``file.txt``.

        3. **Path with subvolume prefix** (``/mnt/temp_root/home/user/...``):
           findmnt returns ``/mnt/temp_root`` with empty FSROOT,
           prefix is stripped.

        Raises ValueError on path traversal attempts (..).
        """
        # Both relative and absolute paths are resolved to absolute
        # first, then converted to subvolume-relative
        if file_path.startswith("/"):
            abs_path = file_path
        else:
            # Resolve relative path against CWD to get absolute path
            # This handles: CWD=/home/user, file_path=file.txt
            # → /home/user/file.txt → (via findmnt /home) → user/file.txt
            try:
                cwd = Path.cwd()
                abs_path = str(cwd / file_path)
            except OSError:
                # CWD unavailable (deleted dir, etc.) — treat as-is
                return self._validate_relative(file_path)

        return self._resolve_absolute_path(abs_path)

    def _resolve_absolute_path(self, abs_path: str) -> str:
        """Convert absolute path to subvolume-relative.

        Strategy:
          1. Walk up the directory tree to find an existing ancestor.
          2. Use findmnt to determine the mount point.
          3. Compute relative path from that mount point.
          4. If mount is top-level (empty FSROOT), strip subvol prefix.
          5. If mount is a specific subvolume, path is already scoped.
        """
        try:
            file_mount = self._find_mount_for_path(abs_path)
            abs_p = Path(abs_path)

            try:
                rel = abs_p.relative_to(file_mount)
            except ValueError:
                try:
                    rel = abs_p.resolve().relative_to(
                        file_mount.resolve()
                    )
                except ValueError:
                    return self._validate_relative(
                        abs_path.lstrip("/")
                    )

            rel_str = str(rel)
            if rel_str == ".":
                rel_str = ""

            try:
                file_fs_root = get_fs_root(str(file_mount))
            except RuntimeError:
                file_fs_root = ""

            if not file_fs_root:
                # Top-level mount — strip subvolume prefix
                rel_str = self._strip_subvol_prefix(rel_str)

            return self._validate_relative(rel_str)

        except (RuntimeError, ValueError):
            return self._validate_relative(abs_path.lstrip("/"))

    def _find_mount_for_path(self, path: str) -> Path:
        """Find mount point by walking up to existing ancestor."""
        p = Path(path)
        candidates = [p] + list(p.parents)
        for candidate in candidates:
            if candidate.exists():
                return find_mount_point(str(candidate))
        return find_mount_point("/")

    def _strip_subvol_prefix(self, rel_path: str) -> str:
        """Strip leading subvolume path from rel_path.

        Only strips non-snapshot subvolume paths. Longest match first.
        """
        if not rel_path:
            return rel_path

        sv_paths = sorted(
            (
                sv.path for sv in self.all_subvolumes
                if sv.path and not sv.is_snapshot
            ),
            key=len,
            reverse=True,
        )

        for sv_path in sv_paths:
            if rel_path == sv_path:
                return ""
            if rel_path.startswith(sv_path + "/"):
                return rel_path[len(sv_path) + 1:]

        return rel_path

    @staticmethod
    def _validate_relative(path: str) -> str:
        """Validate and normalize a relative path string."""
        rel = path.lstrip("/")
        if not rel:
            return ""
        parts = PurePosixPath(rel).parts
        if ".." in parts:
            raise ValueError(
                f"Relative path must not contain '..': {path!r}"
            )
        return str(PurePosixPath(*parts))
