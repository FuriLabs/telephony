.PHONY: all build install clean

all: build

build:
	mkdir -p build/locale
	for po in po/*.po; do \
		lang=$$(basename $$po .po); \
		mkdir -p build/locale/$$lang/LC_MESSAGES; \
		msgfmt $$po -o build/locale/$$lang/LC_MESSAGES/telephony.mo; \
	done

install:
	@echo "Install step handled by debhelper"

clean:
	rm -rf build/locale
	rm -rf build/lib
	rm -rf build/bdist.*
	rm -rf .pybuild
	rm -rf *.egg-info
