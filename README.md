# packages

Collection repository for [gitpkg](https://gitlab.com/fkzys/gitpkg).

## Structure

Each subdirectory is a standalone package with its own `Makefile`:

```
packages/
├── fetch/
│   ├── Makefile
│   └── ...
└── .../
```

## Install via gitpkg

This repository is configured as a default collection in gitpkg.
Packages are installed by name:

```
sudo gitpkg install fetch
```

gitpkg clones the collection once, then builds the requested
package from its subdirectory.

## Manual install

Any package can be installed standalone:

```
cd fetch/
sudo make install
```

## Available packages

| Package | Description |
|---------|-------------|
| [fetch](fetch/) | Minimal system information tool |

## Adding a package

Create a subdirectory with a `Makefile` that has:

- `install` target (required) — must respect `DESTDIR`
- `build` target (optional)

```
packages/
└── mypackage/
    ├── Makefile
    ├── README.md
    └── ...
```

Minimal Makefile:

```makefile
PREFIX  = /usr
DESTDIR =

install:
    install -Dm755 mypackage $(DESTDIR)$(PREFIX)/bin/mypackage

uninstall:
    rm -f $(DESTDIR)$(PREFIX)/bin/mypackage
```

After pushing, the package becomes available to all gitpkg users
who have this collection configured:

```
sudo gitpkg install mypackage
```

## License

GPL-3.0
