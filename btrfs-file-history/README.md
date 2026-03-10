# btrfs-file-history

Track file and directory lifecycle across btrfs snapshots.

Scans all subvolumes visible from a mount point and shows when a file
was created, modified, or deleted — as a colored tree, a timeline
table, Graphviz graph, or JSON.

## Dependencies

- `python3` >= 3.9
- `btrfs-progs`
- `util-linux` (`findmnt`)
- `e2fsprogs` (`filefrag`) — optional, for extent analysis

## Install

```
sudo gitpkg install btrfs-file-history
```

Or manually:

```
cd btrfs-file-history
sudo make install
```

## Usage

All commands require root.

### Subvolume tree

```
sudo btrfs-file-history tree /mnt/btrfs
```

### Track a file across snapshots

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab
sudo btrfs-file-history history /mnt/btrfs /etc/fstab
```

Both absolute and subvolume-relative paths work. Absolute paths are
resolved via `findmnt` automatically.

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab --checksum
sudo btrfs-file-history history /mnt/btrfs etc/fstab --extents
sudo btrfs-file-history history /mnt/btrfs etc/fstab --du
sudo btrfs-file-history history /mnt/btrfs etc/fstab --checksum --extents --du
```

### Filter by snapshot name

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab --filter snapshots/2024-01
```

### Compare two snapshots

```
sudo btrfs-file-history diff /mnt/btrfs etc/fstab snap_old snap_new
```

### Export

```
sudo btrfs-file-history history /mnt/btrfs etc/fstab --format=dot > graph.dot
sudo btrfs-file-history history /mnt/btrfs etc/fstab --format=json
sudo btrfs-file-history tree /mnt/btrfs --format=json
```

## Commands

| Command | Description |
|---------|-------------|
| `tree <mount>` | Show subvolume/snapshot hierarchy |
| `history <mount> <file>` | Track file across snapshots |
| `diff <mount> <file> <old> <new>` | Compare file between two snapshots |

## Options

| Flag | Commands | Effect |
|------|----------|--------|
| `--checksum` | history | BLAKE2b checksum for change detection |
| `--extents` | history | Extent map via filefrag |
| `--du` | history | Shared/exclusive bytes per snapshot |
| `--no-tree` | history | Skip tree, show only timeline |
| `--no-color` | all | Disable colored output |
| `--format` | tree, history | `text`, `dot`, `json` |
| `--filter` | history | Only scan matching subvolumes |

## How it works

1. Discovers subvolumes and snapshots via `btrfs subvolume list`
2. Builds a parent-child tree using UUID linkage
3. Identifies the snapshot family that contains the target file
4. Probes the file in each family member (size, mtime, optional checksum)
5. Computes transitions: creation, modification, deletion, type changes
6. Optionally maps extents via `filefrag` to find shared physical blocks

Only snapshots within the same lineage are scanned, so unrelated
subvolumes don't produce spurious created/deleted alternation.

## Mount point

For best results, mount the btrfs top-level (id=5):

```
sudo mount -o subvolid=5 /dev/sdX /mnt/btrfs
```

Regular subvolume mounts also work — the tool auto-detects the
FSROOT and shows only accessible subvolumes.

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
| `✚` | Created |
| `✎` | Modified |
| `✖` | Deleted |
| `─` | Unchanged |
| `⇋` | Type changed |

## License

GPL-3.0-or-later
