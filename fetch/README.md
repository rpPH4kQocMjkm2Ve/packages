# fetch

Minimal system information tool with gitpkg integration.

## Overview

Displays key system info in a clean terminal format:

```
user@hostname
─────────────
OS                   Arch Linux
Kernel               6.x.x-arch1-1
Uptime               2d 5h 30m
Shell                zsh
CPU                  AMD Ryzen 9 7950X 16-Core Processor
Memory               4096 MiB / 15906 MiB

Packages (gitpkg)    12
Allocator            hardened_malloc
Atomic Upgrade       active
```

## Features

- OS, kernel, uptime, shell, CPU, memory
- gitpkg package count (via `gitpkg list`)
- Memory allocator detection (glibc / hardened_malloc)
- [atomic-upgrade](../atomic-upgrade) detection (via gitpkg or pacman)
- `--paranoid` mode — obscures identifying hardware details:
  - CPU → brand only (e.g. "AMD")
  - Memory → power-of-2 range (e.g. "8-16 GiB")
  - Kernel → "Linux" (no version)

## Dependencies

- `python3` (standard library only)
- `gitpkg` (optional — for package count)
- `pacman` (optional — for atomic-upgrade detection)

## Install

```
sudo make install
```

## Uninstall

```
sudo make uninstall
```

## Usage

```
fetch
fetch --paranoid
```

## Paranoid mode

Reduces fingerprinting by generalizing hardware details:

| Field  | Normal                                    | Paranoid    |
|--------|-------------------------------------------|-------------|
| CPU    | AMD Ryzen 9 7950X 16-Core Processor       | AMD         |
| Memory | 4096 MiB / 15906 MiB                     | 8-16 GiB    |
| Kernel | 6.x.x-arch1-1                             | Linux       |

## Part of

This package is part of the [gitpkg](../README.md) monorepo collection.

## License

AGPL-3.0-or-later
