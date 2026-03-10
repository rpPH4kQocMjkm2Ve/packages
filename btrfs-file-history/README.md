# btrfs-file-history

Track file and directory lifecycle across btrfs snapshots.

Scans all subvolumes visible from a mount point and shows when a file
was created, modified, or deleted — as a colored tree, a timeline
table, Graphviz graph, or JSON.

## Dependencies

- `python3` >= 3.9
- `btrfs-progs` (`btrfs` CLI)
- `util-linux` (`findmnt`)
- `e2fsprogs` (`filefrag`) — optional, for extent analysis

## Install

Via [gitpkg](https://gitlab.com/fkzys/gitpkg):

```
sudo gitpkg install btrfs-file-history
```

Or manually:

```
git clone <url>
cd btrfs-file-history
sudo make install
```

Also installable via pip (for development):

```
pip install -e .
```

## Uninstall

```
sudo gitpkg remove btrfs-file-history
```

or:

```
sudo make uninstall
```

## Usage

All commands require root privileges.

### Show subvolume tree

```
sudo btrfs-file-history tree /mnt/btrfs
```

### Track a file across snapshots

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab
```

The file path is **relative to the subvolume root**, without a leading `/`.

### With more analysis

```
# Checksums for accurate change detection
sudo btrfs-file-history history /mnt/btrfs etc/fstab --checksum

# Shared extent analysis via filefrag
sudo btrfs-file-history history /mnt/btrfs etc/fstab --extents

# Disk usage (exclusive/shared bytes per snapshot)
sudo btrfs-file-history history /mnt/btrfs etc/fstab --du

# All combined
sudo btrfs-file-history history /mnt/btrfs etc/fstab --checksum --extents --du
```

### Filter by snapshot name

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab --filter snapshots/2024-01
```

### Compare two specific snapshots

```
sudo btrfs-file-history diff /mnt/btrfs etc/fstab snap_old snap_new
```

### Export formats

```
# Graphviz DOT
sudo btrfs-file-history history /mnt/btrfs etc/fstab --format=dot > graph.dot
dot -Tpng graph.dot -o graph.png

# JSON
sudo btrfs-file-history history /mnt/btrfs etc/fstab --format=json

# Tree as JSON
sudo btrfs-file-history tree /mnt/btrfs --format=json
```

### Skip the tree, show only timeline

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab --no-tree
```

## Commands

| Command | Description |
|---------|-------------|
| `tree <mount>` | Show subvolume/snapshot hierarchy |
| `history <mount> <file>` | Track file across all snapshots |
| `diff <mount> <file> <old> <new>` | Compare file between two snapshots |

## Options

| Flag | Commands | Effect |
|------|----------|--------|
| `--checksum` | history | BLAKE2b checksum for change detection |
| `--extents` | history | Extent map analysis via filefrag |
| `--du` | history | btrfs disk usage (shared/exclusive) |
| `--no-tree` | history | Skip tree, show only timeline |
| `--no-color` | all | Disable colored output |
| `--format` | tree, history | Output: `text`, `dot`, `json` |
| `--filter PATTERN...` | history | Only scan matching subvolumes |

## How it works

1. Queries `btrfs subvolume list` to discover all subvolumes and snapshots
2. Builds a parent-child tree using UUID linkage
3. Determines the mounted FSROOT via `findmnt` to resolve paths correctly
4. For each accessible snapshot, checks if the target file exists (`lstat`)
5. Compares consecutive states (size, mtime, optional BLAKE2b checksum)
6. Detects: creation, modification, deletion, type changes (file to directory)
7. Optionally maps extents via `filefrag` to find shared physical blocks

## File path argument

The `<file>` argument is relative to the **subvolume root**, not the
system root:

| System path | Mounted subvolume | Argument |
|-------------|-------------------|----------|
| `/etc/fstab` | root subvol on `/` | `etc/fstab` |
| `/home/user/.bashrc` | home subvol on `/home` | `user/.bashrc` |
| `/var/log/syslog` | root subvol on `/` | `var/log/syslog` |

## Mount point

For best results, mount the btrfs top-level (id=5):

```
sudo mount -o subvolid=5 /dev/sdX /mnt/btrfs
sudo btrfs-file-history tree /mnt/btrfs
```

The tool also works with regular subvolume mounts.
It auto-detects the FSROOT and only shows accessible subvolumes.

## Example output

```
rootfs [id=256 ogen=7]
├── snapshots/daily.1 [id=300 ogen=50] ✚ created 1.2KiB
├── snapshots/daily.2 [id=301 ogen=55] ─ unchanged
├── snapshots/daily.3 [id=302 ogen=60] ✎ modified 1.3KiB
└── snapshots/daily.4 [id=303 ogen=65] ─ unchanged

═══ History of: etc/fstab ═══

Subvolume                                Status         Size Mtime
──────────────────────────────────────────────────────────────────
snapshots/daily.1                        ✚ created    1.2KiB 2024-01-15 10:30:00
snapshots/daily.2                        ─ unchanged  1.2KiB 2024-01-15 10:30:00
snapshots/daily.3                        ✎ modified   1.3KiB 2024-01-16 14:22:00
snapshots/daily.4                        ─ unchanged  1.3KiB 2024-01-16 14:22:00

  Created in : snapshots/daily.1
  Modified in: 1 snapshot(s)
  Timeline   : [█─▓─]
```

## Status symbols

| Symbol | Meaning |
|--------|---------|
| `✚` | File created (first appearance or re-creation) |
| `✎` | File modified (content changed) |
| `✖` | File deleted |
| `─` | File unchanged |
| `⇋` | File type changed (e.g. file to directory) |

## Project structure

```
btrfs-file-history/
├── Makefile                     # gitpkg / manual install
├── depends                      # gitpkg dependency declaration
├── pyproject.toml               # pip install support
├── README.md
├── bin/
│   └── btrfs-file-history       # launcher script
└── btrfs_file_history/
    ├── __init__.py
    ├── __main__.py              # python -m support
    ├── btrfs.py                 # btrfs-progs / filefrag wrappers
    ├── tree.py                  # subvolume tree construction
    ├── scanner.py               # file scanning and change detection
    ├── differ.py                # two-snapshot detailed comparison
    ├── cli.py                   # argument parsing and commands
    └── renderer.py              # terminal, Graphviz, JSON output
```

## License

GPL-3.0-or-later
