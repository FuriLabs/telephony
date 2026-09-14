# Copyright (C) 2026 Bardia Moshiri <bardia@furilabs.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from gi.repository import Gio, GLib

from telephony.shared.utils.log_utils import logger


BUS_NAME = "org.gnome.Calls"
ROOT_OBJECT_PATH = "/org/gnome/Calls"
CALL_OBJECT_PATH_PREFIX = "/org/gnome/Calls/Call/"

OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
CALL_INTERFACE = "org.gnome.Calls.Call"
EMERGENCY_INTERFACE = "org.gnome.Calls.EmergencyCalls"

CALL_STATE_UNKNOWN = 0
CALL_STATE_ACTIVE = 1
CALL_STATE_HELD = 2
CALL_STATE_DIALING = 3
CALL_STATE_INCOMING = 5
CALL_STATE_DISCONNECTED = 7

CALL_STATE_MAP = {
    "unknown": CALL_STATE_UNKNOWN,
    "active": CALL_STATE_ACTIVE,
    "held": CALL_STATE_HELD,
    "dialing": CALL_STATE_DIALING,
    "alerting": CALL_STATE_DIALING,
    "incoming": CALL_STATE_INCOMING,
    "waiting": CALL_STATE_INCOMING,
    "disconnected": CALL_STATE_DISCONNECTED,
    "released": CALL_STATE_DISCONNECTED,
    "terminated": CALL_STATE_DISCONNECTED,
}

CALL_PROPERTY_TYPES = {
    "Inbound": "b",
    "State": "u",
    "Id": "s",
    "DisplayName": "s",
    "ImagePath": "s",
    "Protocol": "s",
    "Encrypted": "b",
    "Volte-enabled": "b",
    "CanDtmf": "b",
    "Hints": "a{sv}",
}

EMERGENCY_CONTACT_SOURCE_UNKNOWN = 0


OBJECT_MANAGER_XML = """
<node>
  <interface name="org.freedesktop.DBus.ObjectManager">
    <method name="GetManagedObjects">
      <arg name="objects" type="a{oa{sa{sv}}}" direction="out"/>
    </method>
    <signal name="InterfacesAdded">
      <arg name="object_path" type="o"/>
      <arg name="interfaces_and_properties" type="a{sa{sv}}"/>
    </signal>
    <signal name="InterfacesRemoved">
      <arg name="object_path" type="o"/>
      <arg name="interfaces" type="as"/>
    </signal>
  </interface>
</node>
"""


CALL_XML = """
<node>
  <interface name="org.gnome.Calls.Call">
    <method name="Accept"/>
    <method name="Hangup"/>
    <method name="SendDtmf">
      <arg name="Tone" type="s" direction="in"/>
    </method>
    <method name="Silence"/>
    <property name="Inbound" type="b" access="read"/>
    <property name="State" type="u" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="DisplayName" type="s" access="read"/>
    <property name="ImagePath" type="s" access="read"/>
    <property name="Protocol" type="s" access="read"/>
    <property name="Encrypted" type="b" access="read"/>
    <property name="Volte-enabled" type="b" access="read"/>
    <property name="CanDtmf" type="b" access="read"/>
    <property name="Hints" type="a{sv}" access="read"/>
  </interface>
</node>
"""


EMERGENCY_XML = """
<node>
  <interface name="org.gnome.Calls.EmergencyCalls">
    <method name="GetEmergencyContacts">
      <arg name="contacts" direction="out" type="a(ssia{sv})"/>
    </method>
    <method name="CallEmergencyContact">
      <arg name="id" direction="in" type="s"/>
    </method>
    <signal name="EmergencyNumbersChanged"/>
  </interface>
</node>
"""


class GnomeCallsDBusService:
    """Expose Telephony calls through the GNOME calls D-Bus API"""

    def __init__(self, db, ofono, gsettings_mgr, call_audio):
        self.db = db
        self.ofono = ofono
        self.gsettings_mgr = gsettings_mgr
        self.call_audio = call_audio
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.object_manager_info = Gio.DBusNodeInfo.new_for_xml(OBJECT_MANAGER_XML).interfaces[0]
        self.call_interface_info = Gio.DBusNodeInfo.new_for_xml(CALL_XML).interfaces[0]
        self.emergency_interface_info = Gio.DBusNodeInfo.new_for_xml(EMERGENCY_XML).interfaces[0]

        self.root_registration_id = 0
        self.emergency_registration_id = 0
        self.name_owner_id = 0
        self.next_call_id = 1
        self.calls = {}
        self.gnome_to_ofono = {}
        self.ofono_signal_ids = []
        self.eds_signal_ids = []
        self.gsettings = None
        self.gsettings_signal_id = 0
        self.emergency_snapshot = None

        self.register_root_objects()
        self.connect_sources()
        self.export_existing_calls()
        self.emergency_snapshot = self.emergency_contacts_key()

        self.name_owner_id = Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, self.on_name_acquired, self.on_name_lost)

    def register_root_objects(self):
        """Export ObjectManager and EmergencyCalls at /org/gnome/Calls."""
        self.root_registration_id = self.bus.register_object(ROOT_OBJECT_PATH, self.object_manager_info, self.handle_object_manager_method, None, None)
        if not self.root_registration_id:
            raise RuntimeError("Failed to register org.gnome.Calls ObjectManager")

        self.emergency_registration_id = self.bus.register_object(ROOT_OBJECT_PATH, self.emergency_interface_info, self.handle_emergency_method, None, None)
        if not self.emergency_registration_id:
            self.bus.unregister_object(self.root_registration_id)
            self.root_registration_id = 0
            raise RuntimeError("Failed to register org.gnome.Calls.EmergencyCalls")

        logger.info(f"[GnomeCallsDBus] Registered interfaces at {ROOT_OBJECT_PATH}")

    def connect_sources(self):
        """Follow call, contact and emergency number changes."""
        self.ofono_signal_ids.append(self.ofono.connect("call-added", self.on_call_added))
        self.ofono_signal_ids.append(self.ofono.connect("call-changed", self.on_call_changed))
        self.ofono_signal_ids.append(self.ofono.connect("call-removed", self.on_call_removed))
        self.ofono_signal_ids.append(self.ofono.connect("emergency-numbers-changed", self.on_emergency_source_changed))
        self.ofono_signal_ids.append(self.ofono.connect("ims-state-changed", self.on_ims_state_changed))

        if self.db.eds:
            try:
                self.eds_signal_ids.append(self.db.eds.connect("contacts-loaded", self.on_contacts_changed))
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Could not watch contacts: {e}")

        self.gsettings = self.gsettings_mgr.gsettings if self.gsettings_mgr else None
        if self.gsettings:
            try:
                self.gsettings_signal_id = self.gsettings.connect("changed", self.on_gsettings_changed)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Could not watch settings: {e}")

    def export_existing_calls(self):
        """Export calls that already existed when this service started."""
        for path in list(self.ofono.active_calls.keys()):
            self.export_call(path)

    def on_name_acquired(self, connection, name):
        logger.info(f"[GnomeCallsDBus] Acquired bus name {name}")

    def on_name_lost(self, connection, name):
        logger.error(f"[GnomeCallsDBus] Could not own {name}; another Calls provider may already be running")

    def allocate_call_path(self):
        """Allocate a GNOME Calls object path."""
        path = f"{CALL_OBJECT_PATH_PREFIX}{self.next_call_id}"
        self.next_call_id += 1
        return path

    def lookup_display_name(self, number):
        """Resolve a number against the existing contact cache."""
        if not number:
            return ""

        if not self.db.eds:
            return ""

        try:
            name = self.db.eds.get_contact_name(number)
            if name and name != "Unknown":
                return str(name)
        except Exception as e:
            logger.debug(f"[GnomeCallsDBus] Contact lookup failed for {number}: {e}")

        return ""

    def call_plain_properties(self, ofono_path):
        """Translate one OfonoManager call into GNOME Calls properties."""
        data = self.ofono.active_calls.get(ofono_path, {})
        state = str(data.get("state", "unknown"))
        number = str(data.get("number") or "").strip()
        if number.lower() in ("unknown", "withheld"):
            number = ""

        inbound = state in ("incoming", "waiting") or data.get("direction") == "incoming"
        can_dtmf = bool(self.ofono.voice_proxy and state in ("active", "held"))
        display_name = self.lookup_display_name(number) or str(data.get("name") or "").strip()

        if not display_name:
            display_name = number

        return {
            "Inbound": inbound,
            "State": CALL_STATE_MAP.get(state, CALL_STATE_UNKNOWN),
            "Id": number,
            "DisplayName": display_name,
            "ImagePath": "",
            "Protocol": "tel",
            "Encrypted": False,
            "Volte-enabled": bool(self.ofono.ims_registered and self.ofono.ims_voice_capable),
            "CanDtmf": can_dtmf,
            "Hints": {"ui-active": True},
        }

    def call_property_variant(self, name, value):
        """Convert one call property to its D-Bus variant."""
        if name == "Hints":
            return GLib.Variant("a{sv}", {key: GLib.Variant("b", bool(item)) for key, item in value.items()})
        return GLib.Variant(CALL_PROPERTY_TYPES[name], value)

    def call_variant_properties(self, properties):
        """Convert call properties to a{sv}."""
        return {name: self.call_property_variant(name, value) for name, value in properties.items()}

    def export_call(self, ofono_path):
        """Export an oFono call as org.gnome.Calls.Call."""
        if ofono_path in self.calls:
            self.refresh_call(ofono_path)
            return

        if ofono_path not in self.ofono.active_calls:
            return

        gnome_path = self.allocate_call_path()
        properties = self.call_plain_properties(ofono_path)
        record = {
            "gnome_path": gnome_path,
            "registration_id": 0,
            "properties": properties,
        }

        self.calls[ofono_path] = record
        self.gnome_to_ofono[gnome_path] = ofono_path

        registration_id = self.bus.register_object(gnome_path, self.call_interface_info, self.handle_call_method, self.get_call_property, None)
        if not registration_id:
            self.calls.pop(ofono_path, None)
            self.gnome_to_ofono.pop(gnome_path, None)
            logger.error(f"[GnomeCallsDBus] Failed to register {gnome_path}")
            return

        record["registration_id"] = registration_id

        interfaces = {CALL_INTERFACE: self.call_variant_properties(properties)}
        self.bus.emit_signal(None, ROOT_OBJECT_PATH, OBJECT_MANAGER_INTERFACE, "InterfacesAdded", GLib.Variant("(oa{sa{sv}})", (gnome_path, interfaces)))

        logger.info(f"[GnomeCallsDBus] Exported {ofono_path} as {gnome_path}")

    def unexport_call(self, ofono_path):
        """Remove an ended call."""
        record = self.calls.pop(ofono_path, None)
        if not record:
            return

        gnome_path = record["gnome_path"]
        self.gnome_to_ofono.pop(gnome_path, None)

        self.bus.emit_signal(None, ROOT_OBJECT_PATH, OBJECT_MANAGER_INTERFACE, "InterfacesRemoved", GLib.Variant("(oas)", (gnome_path, [CALL_INTERFACE])))

        if record["registration_id"]:
            self.bus.unregister_object(record["registration_id"])

        logger.info(f"[GnomeCallsDBus] Unexported {ofono_path} from {gnome_path}")

    def refresh_call(self, ofono_path):
        """Refresh an exported call and emit changed properties."""
        record = self.calls.get(ofono_path)
        if not record:
            self.export_call(ofono_path)
            return

        if ofono_path not in self.ofono.active_calls:
            self.unexport_call(ofono_path)
            return

        old = record["properties"]
        new = self.call_plain_properties(ofono_path)
        changed = {name: value for name, value in new.items() if old.get(name) != value}

        if "Id" in changed and "DisplayName" not in changed:
            changed["DisplayName"] = new["DisplayName"]

        record["properties"] = new

        if not changed:
            return

        self.bus.emit_signal(None, record["gnome_path"], PROPERTIES_INTERFACE, "PropertiesChanged",
                             GLib.Variant("(sa{sv}as)", (CALL_INTERFACE, self.call_variant_properties(changed), [])))

    def on_call_added(self, manager, path, props):
        self.export_call(path)

    def on_call_changed(self, manager, path, state):
        self.refresh_call(path)

    def on_call_removed(self, manager, path):
        self.unexport_call(path)

    def on_contacts_changed(self, *args):
        for path in list(self.calls.keys()):
            self.refresh_call(path)

    def on_ims_state_changed(self, manager, registered, voice_capable, sms_capable):
        """Refresh VoLTE state when IMS registration or capabilities change."""
        for path in list(self.calls.keys()):
            self.refresh_call(path)

    def handle_object_manager_method(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Implement org.freedesktop.DBus.ObjectManager."""
        if method_name != "GetManagedObjects":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Method {method_name} is not implemented")
            return

        managed = {}
        for ofono_path, record in self.calls.items():
            if ofono_path in self.ofono.active_calls:
                managed[record["gnome_path"]] = {CALL_INTERFACE: self.call_variant_properties(record["properties"])}

        invocation.return_value(GLib.Variant("(a{oa{sa{sv}}})", (managed,)))

    def get_call_property(self, connection, sender, object_path, interface_name, property_name):
        """Return an org.gnome.Calls.Call property."""
        if interface_name != CALL_INTERFACE:
            return None

        ofono_path = self.gnome_to_ofono.get(object_path)
        if not ofono_path:
            return None

        record = self.calls.get(ofono_path)
        variant_type = CALL_PROPERTY_TYPES.get(property_name)
        if not record or not variant_type:
            return None

        return self.call_property_variant(property_name, record["properties"][property_name])

    def handle_call_method(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Implement org.gnome.Calls.Call methods."""
        if interface_name != CALL_INTERFACE:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownInterface", f"Unknown interface {interface_name}")
            return

        ofono_path = self.gnome_to_ofono.get(object_path)
        if not ofono_path or ofono_path not in self.ofono.active_calls:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownObject", f"Call object {object_path} no longer exists")
            return

        try:
            if method_name == "Accept":
                self.ofono.answer_call(ofono_path)
                invocation.return_value(None)
                return

            if method_name == "Hangup":
                self.ofono.hangup_call(ofono_path)
                invocation.return_value(None)
                return

            if method_name == "SendDtmf":
                tone = str(parameters.unpack()[0])
                if len(tone) != 1 or tone.upper() not in "0123456789ABCD*#":
                    invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "Tone must be one of 0-9, A-D, * or #")
                    return

                self.ofono.send_dtmf(tone.upper())
                invocation.return_value(None)
                return

            if method_name == "Silence":
                data = self.ofono.active_calls.get(ofono_path, {})
                if data.get("state") in ("incoming", "waiting"):
                    data["silenced"] = True
                    self.call_audio.silence_ring()
                invocation.return_value(None)
                return

            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Method {method_name} is not implemented")
        except Exception as e:
            logger.error(f"[GnomeCallsDBus] {method_name} failed for {ofono_path}: {e}")
            invocation.return_dbus_error("org.gnome.Calls.Error.Failed", str(e))

    def emergency_contacts_plain(self):
        """Return the merged emergency list in GNOME Calls format."""
        try:
            entries = self.ofono.get_emergency_numbers()
        except Exception as e:
            logger.warning(f"[GnomeCallsDBus] Failed to load emergency contacts: {e}")
            entries = []

        contacts = []
        seen = set()

        for item in entries or []:
            number = str(item.get("number") or "").strip()
            if not number or number in seen:
                continue

            seen.add(number)
            name = str(item.get("name") or number)
            contacts.append((number, name, EMERGENCY_CONTACT_SOURCE_UNKNOWN, {}))

        return contacts

    def emergency_contacts_key(self):
        """Return a comparable emergency contact snapshot."""
        return tuple((contact_id, name, source) for contact_id, name, source, props in self.emergency_contacts_plain())

    def on_emergency_source_changed(self, *args):
        """Forward emergency contact changes to GNOME Calls."""
        current = self.emergency_contacts_key()
        if current == self.emergency_snapshot:
            return

        self.emergency_snapshot = current
        self.emit_emergency_numbers_changed()

    def on_gsettings_changed(self, settings, key):
        """Check whether a settings change altered the emergency list."""
        self.on_emergency_source_changed()

    def emit_emergency_numbers_changed(self):
        """Announce a changed emergency contact list."""
        self.bus.emit_signal(None, ROOT_OBJECT_PATH, EMERGENCY_INTERFACE, "EmergencyNumbersChanged", None)
        logger.info("[GnomeCallsDBus] Emergency numbers changed")

    def handle_emergency_method(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Implement org.gnome.Calls.EmergencyCalls."""
        if interface_name != EMERGENCY_INTERFACE:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownInterface", f"Unknown interface {interface_name}")
            return

        if method_name == "GetEmergencyContacts":
            invocation.return_value(GLib.Variant("(a(ssia{sv}))", (self.emergency_contacts_plain(),)))
            return

        if method_name == "CallEmergencyContact":
            contact_id = str(parameters.unpack()[0])
            known = {item[0] for item in self.emergency_contacts_plain()}

            if contact_id not in known:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.NotSupported", f"Unknown emergency contact: {contact_id}")
                return

            try:
                if not self.ofono.dial(contact_id):
                    invocation.return_dbus_error("org.gnome.Calls.Error.Failed", f"Failed to dial {contact_id}")
                    return

                invocation.return_value(None)
            except Exception as e:
                logger.error(f"[GnomeCallsDBus] Emergency dial failed for {contact_id}: {e}")
                invocation.return_dbus_error("org.gnome.Calls.Error.Failed", str(e))
            return

        invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Method {method_name} is not implemented")

    def close(self):
        """Unregister the service."""
        for signal_id in self.ofono_signal_ids:
            try:
                self.ofono.disconnect(signal_id)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Failed to disconnect oFono signal {signal_id}: {e}")
        self.ofono_signal_ids.clear()

        if self.db.eds:
            for signal_id in self.eds_signal_ids:
                try:
                    self.db.eds.disconnect(signal_id)
                except Exception as e:
                    logger.debug(f"[GnomeCallsDBus] Failed to disconnect EDS signal {signal_id}: {e}")
        self.eds_signal_ids.clear()

        if self.gsettings and self.gsettings_signal_id:
            try:
                self.gsettings.disconnect(self.gsettings_signal_id)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Failed to disconnect GSettings signal {self.gsettings_signal_id}: {e}")
            self.gsettings_signal_id = 0

        for record in self.calls.values():
            if record["registration_id"]:
                try:
                    self.bus.unregister_object(record["registration_id"])
                except Exception as e:
                    logger.debug(f"[GnomeCallsDBus] Failed to unregister call object {record['gnome_path']}: {e}")

        self.calls.clear()
        self.gnome_to_ofono.clear()

        if self.emergency_registration_id:
            try:
                self.bus.unregister_object(self.emergency_registration_id)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Failed to unregister EmergencyCalls: {e}")
            self.emergency_registration_id = 0

        if self.root_registration_id:
            try:
                self.bus.unregister_object(self.root_registration_id)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Failed to unregister ObjectManager: {e}")
            self.root_registration_id = 0

        if self.name_owner_id:
            try:
                Gio.bus_unown_name(self.name_owner_id)
            except Exception as e:
                logger.debug(f"[GnomeCallsDBus] Failed to release {BUS_NAME}: {e}")
            self.name_owner_id = 0
