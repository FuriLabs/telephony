# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
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

"""CLI commands for Listen."""

from gi.repository import GLib, Gio
from telephony.shared.constants import DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE


def cmd_listen(args):
    """Execute the listen command."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def on_signal(connection, sender_name, object_path, interface_name, signal_name, parameters, user_data):
        if signal_name == "IncomingCall":
            path, number = parameters.unpack()
            print(f"🔔 Incoming Call from {number} ({path})")
        elif signal_name == "CallChanged":
            path, state = parameters.unpack()
            print(f"📞 Call {path} changed state to: {state}")
        elif signal_name == "CallRemoved":
            path = parameters.unpack()[0]
            print(f"📴 Call {path} removed")
        elif signal_name == "IncomingSms":
            number, text = parameters.unpack()
            print(f"✉️ New SMS from {number}: {text}")

    bus.signal_subscribe(
        DAEMON_BUS_NAME,
        DAEMON_INTERFACE,
        None,
        DAEMON_OBJECT_PATH,
        None,
        Gio.DBusSignalFlags.NONE,
        on_signal,
        None
    )
    print("Listening for Telephony events (Ctrl+C to stop)...")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nStopped.")


def setup_parsers(subparsers):
    """Setup argument parsers for listen commands."""
    p_listen = subparsers.add_parser("listen", help="Listen for events (calls, sms)")
    p_listen.set_defaults(func=cmd_listen)
