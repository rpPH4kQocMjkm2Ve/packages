#!/usr/bin/env python3
"""
CLI entry point for btrfs-file-history.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .btrfs import is_btrfs
from .tree import SubvolumeTree
from .scanner import scan_file, probe_file, find_shared_extents
from .renderer import (
    render_subvolume_tree,
    render_file_timeline,
    render_graphviz,
    render_json,
)


def _check_root() -> None:
    """Warn if not running as root."""
    if os.geteuid() != 0:
        print(
            "Warning: btrfs operations usually require root "
            "privileges.\n"
            "Run with sudo if you get permission errors.",
            file=sys.stderr,
        )


def _detect_color(args: argparse.Namespace) -> bool:
    """Determine whether to use colored output."""
    if args.no_color:
        return False
    return sys.stdout.isatty()


def _build_tree(mount_point: str) -> SubvolumeTree:
    """Build subvolume tree with btrfs validation."""
    if not is_btrfs(mount_point):
        print(
            f"Error: {mount_point} does not appear to be a "
            f"btrfs filesystem.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return SubvolumeTree.build(mount_point)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_tree(args: argparse.Namespace) -> None:
    """Show subvolume tree."""
    tree = _build_tree(args.mount_point)
    color = _detect_color(args)

    if args.format == "dot":
        print(render_graphviz(tree))
    elif args.format == "json":
        print(render_json(tree))
    else:
        render_subvolume_tree(tree, color=color)


def cmd_history(args: argparse.Namespace) -> None:
    """Show file history across snapshots."""
    mount = Path(args.mount_point)
    tree = _build_tree(str(mount))
    color = _detect_color(args)

    subvol_filter = args.filter if args.filter else None

    history = scan_file(
        relative_path=args.file_path,
        tree=tree,
        mount_point=mount,
        compute_checksum=args.checksum,
        compute_extents=args.extents,
        compute_du=args.du,
        subvol_filter=subvol_filter,
    )

    if not any(st.exists for st in history.states):
        print(
            f"File '{args.file_path}' not found in any snapshot.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.format == "dot":
        print(render_graphviz(tree, history))
    elif args.format == "json":
        print(render_json(tree, history))
    else:
        if not args.no_tree:
            render_subvolume_tree(
                tree, color=color, history=history,
            )

        render_file_timeline(
            history,
            color=color,
            show_extents=args.du or args.extents,
        )

        if args.extents:
            shared = find_shared_extents(history)
            if shared:
                print(
                    f"  Shared physical extents: {len(shared)}"
                )
                for phys, refs in sorted(shared.items()):
                    names = ", ".join(r[0].name for r in refs)
                    print(
                        f"    offset {phys}: "
                        f"shared by [{names}]"
                    )
                print()


def cmd_diff(args: argparse.Namespace) -> None:
    """Diff a file between two snapshots using probe_file directly."""
    from .differ import diff_states

    mount = Path(args.mount_point)
    tree = _build_tree(str(mount))

    matches_old = tree.find_by_path(args.snap_old)
    matches_new = tree.find_by_path(args.snap_new)

    if not matches_old:
        print(
            f"Snapshot matching '{args.snap_old}' not found.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not matches_new:
        print(
            f"Snapshot matching '{args.snap_new}' not found.",
            file=sys.stderr,
        )
        sys.exit(1)

    sv_old = matches_old[0]
    sv_new = matches_new[0]

    sv_base_old = tree.resolve_subvol_path(sv_old, mount)
    sv_base_new = tree.resolve_subvol_path(sv_new, mount)

    if sv_base_old is None:
        print(
            f"Snapshot '{sv_old.path}' is not accessible "
            f"from this mount point.",
            file=sys.stderr,
        )
        sys.exit(1)
    if sv_base_new is None:
        print(
            f"Snapshot '{sv_new.path}' is not accessible "
            f"from this mount point.",
            file=sys.stderr,
        )
        sys.exit(1)

    state_old = probe_file(
        args.file_path,
        sv_old,
        sv_base_old,
        compute_checksum=True,
        compute_extents=True,
    )
    state_new = probe_file(
        args.file_path,
        sv_new,
        sv_base_new,
        compute_checksum=True,
        compute_extents=True,
    )

    if not state_old.exists and not state_new.exists:
        print(
            "File does not exist in either snapshot.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not state_old.exists:
        print(
            f"File does not exist in old snapshot "
            f"({sv_old.path}); created in {sv_new.path}.",
        )
        return

    if not state_new.exists:
        print(
            f"File does not exist in new snapshot "
            f"({sv_new.path}); deleted after {sv_old.path}.",
        )
        return

    result = diff_states(state_old, state_new, text_diff=True)

    print(f"Size delta    : {result.size_delta:+d} bytes")
    print(f"Content equal : {result.content_identical}")
    print(
        f"Shared extents: {result.shared_extent_count} / "
        f"old={result.old_extent_count} "
        f"new={result.new_extent_count}"
    )

    if result.text_diff:
        print("\n--- Text diff ---")
        print(result.text_diff)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and dispatch to command handlers."""
    parser = argparse.ArgumentParser(
        prog="btrfs-file-history",
        description=(
            "Visualize file/directory lifecycle across "
            "btrfs snapshots."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # -- tree --
    p_tree = sub.add_parser("tree", help="Show subvolume tree")
    p_tree.add_argument("mount_point", help="btrfs mount point")
    p_tree.add_argument(
        "--format",
        choices=["text", "dot", "json"],
        default="text",
    )

    # -- history --
    p_hist = sub.add_parser(
        "history",
        help="Show file history across snapshots",
    )
    p_hist.add_argument("mount_point", help="btrfs mount point")
    p_hist.add_argument(
        "file_path",
        help="File path relative to subvolume root",
    )
    p_hist.add_argument(
        "--checksum",
        action="store_true",
        help="Compute checksums for change detection",
    )
    p_hist.add_argument(
        "--extents",
        action="store_true",
        help="Analyze shared extents via filefrag",
    )
    p_hist.add_argument(
        "--du",
        action="store_true",
        help="Compute btrfs du (shared/exclusive bytes)",
    )
    p_hist.add_argument(
        "--no-tree",
        action="store_true",
        help="Don't show subvolume tree, only timeline",
    )
    p_hist.add_argument(
        "--format",
        choices=["text", "dot", "json"],
        default="text",
    )
    p_hist.add_argument(
        "--filter",
        nargs="+",
        metavar="PATTERN",
        help="Only scan subvolumes matching pattern(s)",
    )

    # -- diff --
    p_diff = sub.add_parser(
        "diff",
        help="Diff a file between two snapshots",
    )
    p_diff.add_argument("mount_point", help="btrfs mount point")
    p_diff.add_argument(
        "file_path",
        help="File path relative to subvolume root",
    )
    p_diff.add_argument(
        "snap_old", help="Old snapshot (path fragment)",
    )
    p_diff.add_argument(
        "snap_new", help="New snapshot (path fragment)",
    )

    args = parser.parse_args()
    _check_root()

    handlers = {
        "tree": cmd_tree,
        "history": cmd_history,
        "diff": cmd_diff,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
