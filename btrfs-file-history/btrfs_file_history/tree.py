"""
Build a tree structure from flat subvolume list using parent_uuid linkage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .btrfs import Subvolume, list_subvolumes, get_fs_root


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
    fs_root: str  # FSROOT of the mount point (empty for top-level)

    @classmethod
    def build(cls, mount_point: str) -> SubvolumeTree:
        """Build tree from a mounted btrfs filesystem.

        Children lists are rebuilt from scratch each time so that
        calling ``build()`` twice does not duplicate entries.
        """
        subvols = list_subvolumes(mount_point)

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

        # Reset children so repeated builds don't accumulate
        for sv in subvols:
            sv.children = []

        roots: list[Subvolume] = []
        for sv in subvols:
            if sv.parent_uuid and sv.parent_uuid in by_uuid:
                parent = by_uuid[sv.parent_uuid]
                parent.children.append(sv)
            else:
                roots.append(sv)

        # Sort children by ogen once; renderers rely on this order
        for sv in subvols:
            sv.children.sort(key=lambda c: c.ogen)

        return cls(
            roots=roots,
            by_uuid=by_uuid,
            by_id=by_id,
            all_subvolumes=subvols,
            fs_root=fs_root,
        )

    def iter_depth_first(
        self,
    ) -> Iterator[tuple[int, Subvolume, bool]]:
        """Yield ``(depth, subvolume, is_last_child)`` depth-first.

        *is_last_child* is needed for choosing ``└──`` vs ``├──``
        when rendering tree lines.
        """
        def _walk(
            sv: Subvolume,
            depth: int,
            is_last: bool,
        ) -> Iterator[tuple[int, Subvolume, bool]]:
            yield (depth, sv, is_last)
            for i, child in enumerate(sv.children):
                yield from _walk(
                    child, depth + 1, i == len(sv.children) - 1,
                )

        for i, root in enumerate(self.roots):
            yield from _walk(root, 0, i == len(self.roots) - 1)

    def get_lineage(self, sv: Subvolume) -> list[Subvolume]:
        """Return the chain from root to *sv* (inclusive)."""
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

    def resolve_subvol_path(
        self,
        sv: Subvolume,
        mount_point: Path,
    ) -> Optional[Path]:
        """Resolve the actual filesystem path for a subvolume.

        When a non-top-level subvolume is mounted (e.g. FSROOT = "@"),
        only subvolumes whose paths start with that root are accessible.
        Returns None if the subvolume is not reachable from this mount.
        """
        sv_path = sv.path

        if self.fs_root:
            if sv_path == self.fs_root:
                return mount_point
            if sv_path.startswith(self.fs_root + "/"):
                relative = sv_path[len(self.fs_root) + 1:]
                return mount_point / relative
            return None

        # Top-level (id=5) is mounted — all subvolumes are accessible
        return mount_point / sv_path
