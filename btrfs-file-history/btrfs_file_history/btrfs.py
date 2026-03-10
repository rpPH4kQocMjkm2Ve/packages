"""
Wrapper around btrfs-progs CLI utilities.

External dependencies:
  - btrfs (btrfs-progs)
  - findmnt (util-linux)
  - filefrag (e2fsprogs) — optional, used for extent mapping
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Subvolume:
    """Single btrfs subvolume or snapshot."""

    subvol_id: int
    gen: int
    ogen: int                          # origin generation (creation gen)
    top_level: int
    path: str
    uuid: str
    parent_uuid: Optional[str]
    received_uuid: Optional[str]
    is_snapshot: bool = False

    # Populated during tree construction, not at __init__ time
    mount_point: Optional[Path] = None
    children: list[Subvolume] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.path).name or self.path

    def __hash__(self) -> int:
        return hash(self.uuid)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Subvolume):
            return self.uuid == other.uuid
        return NotImplemented

    def __repr__(self) -> str:
        return f"Subvolume(id={self.subvol_id}, path={self.path!r})"


@dataclass
class ExtentInfo:
    """One file extent as reported by filefrag."""

    logical_offset: int    # blocks
    physical_offset: int   # blocks
    length: int            # blocks
    flags: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an external command and return CompletedProcess.

    Raises RuntimeError with a human-readable message on failure.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Required utility not found: {cmd[0]!r}. "
            f"Make sure it is installed and in PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"Command failed (exit {exc.returncode}): "
            f"{' '.join(cmd)}\n{stderr}"
        ) from exc


_UUID_NONE = "-"


def _uuid_or_none(val: str) -> Optional[str]:
    """Return None for missing / placeholder UUID values."""
    val = val.strip()
    if not val or val == _UUID_NONE:
        return None
    return val


def _has_filefrag() -> bool:
    """Check whether filefrag is available on this system."""
    return shutil.which("filefrag") is not None


# ---------------------------------------------------------------------------
# Subvolume listing — token-based parser
# ---------------------------------------------------------------------------

_TWO_WORD_KEYS = frozenset({"top", "received"})


def _parse_subvol_line(line: str) -> Optional[dict[str, str]]:
    """Parse one line of ``btrfs subvolume list`` output into a dict.

    Uses a token-based approach that handles varying field order and
    optional fields across btrfs-progs versions.

    Expected tokens (order may vary)::

        ID <n>  gen <n>  cgen <n>  top level <n>
        parent_uuid <uuid>  received_uuid <uuid>  uuid <uuid>
        path <rest-of-line>
    """
    tokens = line.split()
    if len(tokens) < 4:
        return None

    result: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i].lower().rstrip(":")

        # "path" consumes the rest of the line (paths may contain spaces)
        if token == "path":
            result["path"] = " ".join(tokens[i + 1:])
            break

        # Two-word keys: "top level", "received uuid"
        if token in _TWO_WORD_KEYS and i + 2 < len(tokens):
            second = tokens[i + 1].lower().rstrip(":")
            combined = f"{token}_{second}"
            result[combined] = tokens[i + 2]
            i += 3
            continue

        if i + 1 < len(tokens):
            key = "ogen" if token == "cgen" else token
            result[key] = tokens[i + 1]
            i += 2
        else:
            i += 1

    return result if "id" in result and "path" in result else None


def list_subvolumes(mount_point: str | Path) -> list[Subvolume]:
    """List all subvolumes via ``btrfs subvolume list``.

    Uses flags ``-puqRgc`` (parent, uuid, parent-uuid, received-uuid,
    generation, cgen) with ``--sort=ogen`` for chronological order.
    Falls back to minimal flags if the full set is not supported.
    """
    mount_str = str(mount_point)

    full_cmd = [
        "btrfs", "subvolume", "list",
        "-p", "-u", "-q", "-R", "-g", "-c",
        "--sort=ogen",
        mount_str,
    ]

    try:
        result = _run(full_cmd)
    except RuntimeError:
        fallback_cmd = [
            "btrfs", "subvolume", "list",
            "-p", "-u", "-q",
            mount_str,
        ]
        result = _run(fallback_cmd)

    subvols: list[Subvolume] = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        parsed = _parse_subvol_line(line)
        if parsed is None:
            continue

        # Strip <FS_TREE>/ prefix that may appear with -a flag
        path = parsed.get("path", "")
        if path.startswith("<FS_TREE>/"):
            path = path[len("<FS_TREE>/"):]

        parent_uuid = _uuid_or_none(parsed.get("parent_uuid", "-"))

        try:
            sv = Subvolume(
                subvol_id=int(parsed["id"]),
                gen=int(parsed.get("gen", "0")),
                ogen=int(parsed.get("ogen", parsed.get("gen", "0"))),
                top_level=int(parsed.get("top_level", "0")),
                path=path,
                uuid=parsed.get("uuid", ""),
                parent_uuid=parent_uuid,
                received_uuid=_uuid_or_none(
                    parsed.get("received_uuid", "-")
                ),
                is_snapshot=(parent_uuid is not None),
            )
        except (ValueError, KeyError):
            continue

        subvols.append(sv)

    subvols.sort(key=lambda s: s.ogen)
    return subvols


# ---------------------------------------------------------------------------
# Subvolume info
# ---------------------------------------------------------------------------

def subvolume_show(path: str | Path) -> dict[str, str]:
    """Parse output of ``btrfs subvolume show <path>`` into a dict."""
    result = _run(["btrfs", "subvolume", "show", str(path)])
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            info[key.strip()] = val.strip()
    return info


# ---------------------------------------------------------------------------
# Filesystem disk usage
# ---------------------------------------------------------------------------

def filesystem_du(path: str | Path) -> dict[str, int]:
    """Return total / exclusive / set_shared bytes for a path.

    Parses ``btrfs filesystem du -s --raw``.
    Returns zeroes on failure instead of raising.
    """
    result = _run(
        ["btrfs", "filesystem", "du", "-s", "--raw", str(path)],
        check=False,
    )

    info: dict[str, int] = {"total": 0, "exclusive": 0, "set_shared": 0}
    if result.returncode != 0:
        return info

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            try:
                info["total"] = int(parts[0])
                info["exclusive"] = int(parts[1])
                info["set_shared"] = int(parts[2])
            except ValueError:
                pass
            break

    return info


# ---------------------------------------------------------------------------
# Extent mapping via filefrag — compiled regex parser
# ---------------------------------------------------------------------------

# Matches: <ext_idx>: <log_start>..<log_end>: <phys_start>..<phys_end>:
_EXTENT_RE = re.compile(
    r"^\s*\d+:\s+"
    r"(\d+)\.\.\s*(\d+):\s+"    # logical range  (groups 1, 2)
    r"(\d+)\.\.\s*(\d+):\s+"    # physical range (groups 3, 4)
)


def get_extents(path: str | Path) -> list[ExtentInfo]:
    """Get file extent map using ``filefrag -v``.

    Returns an empty list if filefrag is not installed or the command
    fails (e.g. on inline-data files, special files).
    """
    if not _has_filefrag():
        return []

    result = _run(["filefrag", "-v", str(path)], check=False)
    if result.returncode != 0:
        return []

    extents: list[ExtentInfo] = []

    for line in result.stdout.splitlines():
        m = _EXTENT_RE.match(line)
        if not m:
            continue

        log_start = int(m.group(1))
        log_end = int(m.group(2))
        phys_start = int(m.group(3))
        length = log_end - log_start + 1

        # Flags sit in the last colon-separated segment
        segments = line.split(":")
        flags = ""
        if segments:
            tail = segments[-1].strip()
            if tail and not tail.isdigit():
                flags = tail

        extents.append(ExtentInfo(
            logical_offset=log_start,
            physical_offset=phys_start,
            length=length,
            flags=flags,
        ))

    return extents


# ---------------------------------------------------------------------------
# Mount point detection
# ---------------------------------------------------------------------------

def find_mount_point(path: str | Path) -> Path:
    """Determine the btrfs mount point for a given path."""
    path = Path(path).resolve()
    result = _run(["findmnt", "-n", "-o", "TARGET", "-T", str(path)])
    target = result.stdout.strip()
    if not target:
        raise RuntimeError(f"Could not determine mount point for {path}")
    return Path(target)


def get_fs_root(mount_point: str | Path) -> str:
    """Get the FSROOT (mounted subvolume path) for a mount point.

    Returns path relative to filesystem top-level (no leading slash).
    For top-level (id=5) mounts this returns an empty string.
    """
    result = _run([
        "findmnt", "-n", "-o", "FSROOT", "-T", str(mount_point),
    ])
    return result.stdout.strip().lstrip("/")


def is_btrfs(path: str | Path) -> bool:
    """Check whether the given path resides on a btrfs filesystem."""
    result = _run(
        ["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)],
        check=False,
    )
    return (
        result.returncode == 0
        and "btrfs" in result.stdout.strip().lower()
    )


# ---------------------------------------------------------------------------
# Send/receive dump
# ---------------------------------------------------------------------------

def send_dump(
    parent_snap: str | Path,
    child_snap: str | Path,
    *,
    timeout: int = 120,
) -> str:
    """Run btrfs send/receive dump pipeline.

    Both processes are cleaned up properly on timeout or failure.
    """
    send_proc: Optional[subprocess.Popen[bytes]] = None
    recv_proc: Optional[subprocess.Popen[bytes]] = None

    try:
        send_proc = subprocess.Popen(
            ["btrfs", "send", "--no-data",
             "-p", str(parent_snap), str(child_snap)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        recv_proc = subprocess.Popen(
            ["btrfs", "receive", "--dump"],
            stdin=send_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Allow send_proc to receive SIGPIPE if recv_proc exits early
        assert send_proc.stdout is not None
        send_proc.stdout.close()

        assert recv_proc.stdout is not None
        out, recv_err = recv_proc.communicate(timeout=timeout)
        send_proc.wait(timeout=30)

        if recv_proc.returncode != 0:
            err_msg = recv_err.decode(errors="replace").strip()
            raise RuntimeError(
                f"btrfs receive --dump failed: {err_msg}"
            )

        return out.decode(errors="replace")

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"send/receive dump timed out after {timeout}s"
        )

    finally:
        for proc in (send_proc, recv_proc):
            if proc is not None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except OSError:
                    pass
