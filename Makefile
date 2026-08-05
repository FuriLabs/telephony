PREFIX ?= /usr

LIB_DIR = $(PREFIX)/lib
BIN_DIR = $(PREFIX)/bin
DATA_DIR = $(PREFIX)/share

INSTALL_DIR = $(LIB_DIR)/furios-app-telephony
APPLICATIONS_DIR = $(DATA_DIR)/applications
DBUS_SERVICE_DIR = $(DATA_DIR)/dbus-1/services
METAINFO_DIR = $(DATA_DIR)/metainfo
ICON_DIR = $(DATA_DIR)/icons/hicolor/scalable
SYSTEMD_USER_DIR = $(LIB_DIR)/systemd/user
LOCALE_DIR = $(DATA_DIR)/locale
GSCHEMA_DIR = $(DATA_DIR)/glib-2.0/schemas

.PHONY: all build install uninstall clean

all: build

build:
	mkdir -p build/locale
	for po in po/*.po; do \
		lang=$$(basename "$$po" .po); \
		mkdir -p "build/locale/$$lang/LC_MESSAGES"; \
		msgfmt "$$po" -o "build/locale/$$lang/LC_MESSAGES/telephony.mo"; \
	done

install: build
	install -d $(DESTDIR)$(INSTALL_DIR)
	install -d $(DESTDIR)$(BIN_DIR)
	install -d $(DESTDIR)$(APPLICATIONS_DIR)
	install -d $(DESTDIR)$(DBUS_SERVICE_DIR)
	install -d $(DESTDIR)$(METAINFO_DIR)
	install -d $(DESTDIR)$(ICON_DIR)/apps
	install -d $(DESTDIR)$(ICON_DIR)/actions
	install -d $(DESTDIR)$(ICON_DIR)/devices
	install -d $(DESTDIR)$(ICON_DIR)/mimetypes
	install -d $(DESTDIR)$(ICON_DIR)/places
	install -d $(DESTDIR)$(ICON_DIR)/status
	install -d $(DESTDIR)$(SYSTEMD_USER_DIR)
	install -d $(DESTDIR)$(LOCALE_DIR)
	install -d $(DESTDIR)$(GSCHEMA_DIR)

	cp -r src/telephony $(DESTDIR)$(INSTALL_DIR)/

	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony
	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Daemon
	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Calls
	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Messages
	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Contacts
	ln -sf ../lib/furios-app-telephony/telephony/main.py $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Incall
	ln -sf ../lib/furios-app-telephony/telephony/cli/cli_main.py $(DESTDIR)$(BIN_DIR)/telephony-cli

	install -m 644 data/io.furios.Telephony.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Calls.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Messages.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Contacts.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Incall.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Emergency.desktop $(DESTDIR)$(APPLICATIONS_DIR)/
	install -m 644 data/io.furios.Telephony.Daemon.desktop $(DESTDIR)$(APPLICATIONS_DIR)/

	install -m 644 data/io.furios.Telephony.Daemon.service $(DESTDIR)$(DBUS_SERVICE_DIR)/
	install -m 644 data/io.furios.Telephony.metainfo.xml $(DESTDIR)$(METAINFO_DIR)/

	install -m 644 data/io.furios.Telephony.svg $(DESTDIR)$(ICON_DIR)/apps/
	install -m 644 data/io.furios.Telephony.Calls.svg $(DESTDIR)$(ICON_DIR)/apps/
	install -m 644 data/io.furios.Telephony.Contacts.svg $(DESTDIR)$(ICON_DIR)/apps/
	install -m 644 data/io.furios.Telephony.Messages.svg $(DESTDIR)$(ICON_DIR)/apps/
	install -m 644 data/io.furios.Telephony.Emergency.svg $(DESTDIR)$(ICON_DIR)/apps/

	cp -a data/icons/actions/. $(DESTDIR)$(ICON_DIR)/actions/
	cp -a data/icons/devices/. $(DESTDIR)$(ICON_DIR)/devices/
	cp -a data/icons/mimetypes/. $(DESTDIR)$(ICON_DIR)/mimetypes/
	cp -a data/icons/places/. $(DESTDIR)$(ICON_DIR)/places/
	cp -a data/icons/status/. $(DESTDIR)$(ICON_DIR)/status/

	install -m 644 data/telephony.service $(DESTDIR)$(SYSTEMD_USER_DIR)/
	install -m 644 data/io.furios.Telephony.gschema.xml $(DESTDIR)$(GSCHEMA_DIR)/

	cp -a build/locale/. $(DESTDIR)$(LOCALE_DIR)/

uninstall:
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Daemon
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Calls
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Messages
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Contacts
	rm -f $(DESTDIR)$(BIN_DIR)/io.furios.Telephony.Incall
	rm -f $(DESTDIR)$(BIN_DIR)/telephony-cli
	rm -rf $(DESTDIR)$(INSTALL_DIR)

	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Daemon.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Calls.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Messages.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Contacts.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Incall.desktop
	rm -f $(DESTDIR)$(APPLICATIONS_DIR)/io.furios.Telephony.Emergency.desktop

	rm -f $(DESTDIR)$(DBUS_SERVICE_DIR)/io.furios.Telephony.Daemon.service
	rm -f $(DESTDIR)$(METAINFO_DIR)/io.furios.Telephony.metainfo.xml

	rm -f $(DESTDIR)$(ICON_DIR)/apps/io.furios.Telephony.svg
	rm -f $(DESTDIR)$(ICON_DIR)/apps/io.furios.Telephony.Calls.svg
	rm -f $(DESTDIR)$(ICON_DIR)/apps/io.furios.Telephony.Contacts.svg
	rm -f $(DESTDIR)$(ICON_DIR)/apps/io.furios.Telephony.Messages.svg
	rm -f $(DESTDIR)$(ICON_DIR)/apps/io.furios.Telephony.Emergency.svg

	rm -f $(DESTDIR)$(SYSTEMD_USER_DIR)/telephony.service
	rm -f $(DESTDIR)$(GSCHEMA_DIR)/io.furios.Telephony.gschema.xml

	for directory in actions devices mimetypes places status; do \
		if [ -d "data/icons/$$directory" ]; then \
			find "data/icons/$$directory" -type f -printf '%P\n' | while read -r file; do \
				rm -f "$(DESTDIR)$(ICON_DIR)/$$directory/$$file"; \
			done; \
		fi; \
	done

	for po in po/*.po; do \
		lang=$$(basename "$$po" .po); \
		rm -f "$(DESTDIR)$(LOCALE_DIR)/$$lang/LC_MESSAGES/telephony.mo"; \
	done

clean:
	rm -rf build/locale
