PREFIX  = /usr
DESTDIR =

PKGNAME   = btrfs-file-history
PYPACKAGE = btrfs_file_history
LIBDIR    = $(DESTDIR)$(PREFIX)/lib/$(PKGNAME)
BINDIR    = $(DESTDIR)$(PREFIX)/bin

.PHONY: build install uninstall

build:
	@echo "Pure Python — nothing to compile"

install:
	install -d $(LIBDIR)/$(PYPACKAGE)
	install -d $(BINDIR)
	install -m644 $(PYPACKAGE)/__init__.py  $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/__main__.py  $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/cli.py       $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/btrfs.py     $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/tree.py      $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/scanner.py   $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/differ.py    $(LIBDIR)/$(PYPACKAGE)/
	install -m644 $(PYPACKAGE)/renderer.py  $(LIBDIR)/$(PYPACKAGE)/
	install -m755 bin/$(PKGNAME)            $(BINDIR)/$(PKGNAME)

uninstall:
	rm -f  $(BINDIR)/$(PKGNAME)
	rm -rf $(LIBDIR)
