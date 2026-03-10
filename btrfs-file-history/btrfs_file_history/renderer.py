"""
Output formatting: terminal (colored ASCII tree, tables),
Graphviz DOT, JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from io import StringIO
from typing import Optional, TextIO

from .btrfs import Subvolume
from .scanner import FileHistory, FileState, FileTransition
from .tree import SubvolumeTree


# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

class _C:
    """ANSI escape codes."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"


def _color(text: str, code: str, *, enabled: bool = True) -> str:
    """Wrap text in ANSI color codes if enabled."""
    if not enabled:
        return text
    return f"{code}{text}{_C.RESET}"


def _pad_colored(
    plain: str,
    width: int,
    code: str,
    *,
    enabled: bool = True,
    align: str = "<",
) -> str:
    """Pad plain text to width, then wrap in color.

    Padding is computed on the plain string so that ANSI escape
    sequences do not break column alignment.
    """
    if align == ">":
        padded = plain.rjust(width)
    elif align == "^":
        padded = plain.center(width)
    else:
        padded = plain.ljust(width)
    return _color(padded, code, enabled=enabled)


_CHANGE_STYLE: dict[str, tuple[str, str]] = {
    "created":      ("✚", _C.GREEN),
    "modified":     ("✎", _C.YELLOW),
    "deleted":      ("✖", _C.RED),
    "unchanged":    ("─", _C.DIM),
    "type_changed": ("⇋", _C.MAGENTA),
}

_STATUS_BLOCKS: dict[str, str] = {
    "created":      "█",
    "modified":     "▓",
    "deleted":      "░",
    "unchanged":    "─",
    "type_changed": "▒",
}


# ---------------------------------------------------------------------------
# Human-readable sizes
# ---------------------------------------------------------------------------

def _human_size(n: Optional[int]) -> str:
    """Format byte count as human-readable string."""
    if n is None:
        return "—"
    value: float = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}PiB"


# ---------------------------------------------------------------------------
# Subvolume tree rendering
# ---------------------------------------------------------------------------

def render_subvolume_tree(
    tree: SubvolumeTree,
    *,
    out: TextIO = sys.stdout,
    color: bool = True,
    history: Optional[FileHistory] = None,
) -> None:
    """Render ASCII tree with ``└──`` / ``├──`` connectors."""

    state_by_uuid: dict[str, FileState] = {}
    trans_by_uuid: dict[str, str] = {}
    if history:
        for st in history.states:
            state_by_uuid[st.subvolume.uuid] = st
        for tr in history.transitions:
            trans_by_uuid[tr.curr.subvolume.uuid] = tr.change_type

    def _render(
        sv: Subvolume,
        prefix: str,
        is_last: bool,
        is_root: bool,
    ) -> None:
        if is_root:
            connector = ""
            child_prefix = prefix
        else:
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + (
                "    " if is_last else "│   "
            )

        label = sv.name or sv.path
        if sv.is_snapshot:
            label_str = _color(label, _C.CYAN, enabled=color)
        else:
            label_str = _color(
                label, _C.BOLD + _C.WHITE, enabled=color,
            )

        meta = _color(
            f"[id={sv.subvol_id} ogen={sv.ogen}]",
            _C.DIM,
            enabled=color,
        )

        annotation = ""
        if sv.uuid in trans_by_uuid:
            change = trans_by_uuid[sv.uuid]
            sym, clr = _CHANGE_STYLE.get(change, ("?", _C.WHITE))
            st = state_by_uuid.get(sv.uuid)
            size_str = ""
            if st and st.size is not None:
                size_str = f" {_human_size(st.size)}"
            annotation = " " + _color(
                f"{sym} {change}{size_str}",
                clr,
                enabled=color,
            )

        out.write(
            f"{prefix}{connector}{label_str} {meta}{annotation}\n"
        )

        # Children are pre-sorted by ogen in tree.build()
        for i, child in enumerate(sv.children):
            _render(
                child,
                child_prefix,
                i == len(sv.children) - 1,
                False,
            )

    for i, root in enumerate(tree.roots):
        _render(root, "", i == len(tree.roots) - 1, True)


# ---------------------------------------------------------------------------
# File timeline table
# ---------------------------------------------------------------------------

_COL_SUBVOL = 40
_COL_STATUS = 14
_COL_SIZE   = 10
_COL_MTIME  = 20
_COL_BYTES  = 10


def render_file_timeline(
    history: FileHistory,
    *,
    out: TextIO = sys.stdout,
    color: bool = True,
    show_extents: bool = False,
) -> None:
    """Render a chronological table of file changes."""

    out.write(_color(
        f"\n═══ History of: {history.relative_path} ═══\n\n",
        _C.BOLD,
        enabled=color,
    ))

    header_parts = [
        "Subvolume".ljust(_COL_SUBVOL),
        "Status".ljust(_COL_STATUS),
        "Size".rjust(_COL_SIZE),
        "Mtime".ljust(_COL_MTIME),
    ]
    if show_extents:
        header_parts.append("Exclusive".rjust(_COL_BYTES))
        header_parts.append("Shared".rjust(_COL_BYTES))

    header_line = " ".join(header_parts)
    out.write(
        _color(header_line, _C.BOLD + _C.BLUE, enabled=color) + "\n"
    )
    out.write("─" * len(header_line) + "\n")

    for tr in history.transitions:
        st = tr.curr
        sym, clr = _CHANGE_STYLE.get(
            tr.change_type, ("?", _C.WHITE),
        )

        sv_name = _truncate(st.subvolume.path, _COL_SUBVOL - 2)
        status_plain = f"{sym} {tr.change_type}"

        size_str = (
            _human_size(st.size) if st.size is not None else "—"
        )
        mtime_str = (
            datetime.fromtimestamp(st.mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if st.mtime
            else "—"
        )

        parts = [
            sv_name.ljust(_COL_SUBVOL),
            _pad_colored(
                status_plain, _COL_STATUS, clr, enabled=color,
            ),
            size_str.rjust(_COL_SIZE),
            mtime_str.ljust(_COL_MTIME),
        ]

        if show_extents:
            excl = (
                _human_size(st.exclusive_bytes)
                if st.exclusive_bytes is not None
                else "—"
            )
            shared = (
                _human_size(st.shared_bytes)
                if st.shared_bytes is not None
                else "—"
            )
            parts.append(excl.rjust(_COL_BYTES))
            parts.append(shared.rjust(_COL_BYTES))

        out.write(" ".join(parts) + "\n")

    out.write("\n")
    created = history.created_in
    modified = history.modified_in
    out.write(
        f"  Created in : "
        f"{created.path if created else '(not found)'}\n"
    )
    out.write(f"  Modified in: {len(modified)} snapshot(s)\n")

    bar_parts: list[str] = []
    for tr in history.transitions:
        block = _STATUS_BLOCKS.get(tr.change_type, "?")
        _, clr = _CHANGE_STYLE.get(
            tr.change_type, ("?", _C.WHITE),
        )
        bar_parts.append(_color(block, clr, enabled=color))
    bar = "".join(bar_parts)
    out.write(f"  Timeline   : [{bar}]\n\n")


# ---------------------------------------------------------------------------
# Graphviz DOT output
# ---------------------------------------------------------------------------

_DOT_COLORS: dict[str, str] = {
    "created":      "#22c55e",
    "modified":     "#eab308",
    "deleted":      "#ef4444",
    "unchanged":    "#9ca3af",
    "type_changed": "#a855f7",
}

_DOT_FILLS: dict[str, str] = {
    "created":      "#dcfce7",
    "modified":     "#fef9c3",
    "deleted":      "#fee2e2",
    "type_changed": "#f3e8ff",
}


def render_graphviz(
    tree: SubvolumeTree,
    history: Optional[FileHistory] = None,
) -> str:
    """Generate a Graphviz DOT file for the subvolume tree."""
    trans_by_uuid: dict[str, str] = {}
    state_by_uuid: dict[str, FileState] = {}
    if history:
        for tr in history.transitions:
            trans_by_uuid[tr.curr.subvolume.uuid] = tr.change_type
        for st in history.states:
            state_by_uuid[st.subvolume.uuid] = st

    dot = StringIO()
    dot.write("digraph btrfs_snapshots {\n")
    dot.write("  rankdir=TB;\n")
    dot.write(
        '  node [shape=box, style=rounded, '
        'fontname="monospace"];\n'
    )
    dot.write("  edge [color=gray];\n\n")

    for sv in tree.all_subvolumes:
        node_id = _dot_id(sv.uuid)
        label = _dot_escape(
            f"{sv.name}\\nid={sv.subvol_id} ogen={sv.ogen}"
        )

        fill = "#f3f4f6"
        border = "#6b7280"

        if sv.uuid in trans_by_uuid:
            change = trans_by_uuid[sv.uuid]
            border = _DOT_COLORS.get(change, "#6b7280")
            fill = _DOT_FILLS.get(change, fill)

            st = state_by_uuid.get(sv.uuid)
            extra = change
            if st and st.size is not None:
                extra = f"{change} ({_human_size(st.size)})"
            label = _dot_escape(
                f"{sv.name}\\nid={sv.subvol_id} "
                f"ogen={sv.ogen}\\n{extra}"
            )

        dot.write(
            f'  "{node_id}" '
            f'[label="{label}", fillcolor="{fill}", '
            f'style="rounded,filled", '
            f'color="{border}", penwidth=2];\n'
        )

    dot.write("\n")

    for sv in tree.all_subvolumes:
        if sv.parent_uuid and sv.parent_uuid in tree.by_uuid:
            src = _dot_id(sv.parent_uuid)
            dst = _dot_id(sv.uuid)
            dot.write(f'  "{src}" -> "{dst}";\n')

    dot.write("}\n")
    return dot.getvalue()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def render_json(
    tree: SubvolumeTree,
    history: Optional[FileHistory] = None,
) -> str:
    """Generate JSON representation of the tree and file history."""
    trans_by_uuid: dict[str, str] = {}
    state_by_uuid: dict[str, FileState] = {}
    if history:
        for tr in history.transitions:
            trans_by_uuid[tr.curr.subvolume.uuid] = tr.change_type
        for st in history.states:
            state_by_uuid[st.subvolume.uuid] = st

    nodes: list[dict] = []
    for sv in tree.all_subvolumes:
        node: dict = {
            "id": sv.subvol_id,
            "uuid": sv.uuid,
            "parent_uuid": sv.parent_uuid,
            "path": sv.path,
            "ogen": sv.ogen,
            "is_snapshot": sv.is_snapshot,
        }
        if sv.uuid in trans_by_uuid:
            node["file_status"] = trans_by_uuid[sv.uuid]
            st = state_by_uuid.get(sv.uuid)
            if st:
                node["file_size"] = st.size
                node["file_mtime"] = st.mtime
                if st.exclusive_bytes is not None:
                    node["exclusive_bytes"] = st.exclusive_bytes
                if st.shared_bytes is not None:
                    node["shared_bytes"] = st.shared_bytes
        nodes.append(node)

    data = {
        "file": history.relative_path if history else None,
        "subvolumes": nodes,
    }
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _dot_id(uuid: str) -> str:
    """Convert a UUID to a valid Graphviz node identifier."""
    return uuid.replace("-", "_")


def _dot_escape(text: str) -> str:
    """Escape special characters for Graphviz labels."""
    return text.replace('"', '\\"')


def _truncate(text: str, max_width: int) -> str:
    """Truncate text with ellipsis if longer than *max_width*."""
    if len(text) <= max_width:
        return text
    return text[: max_width - 1] + "…"
