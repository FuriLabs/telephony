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

import html
import re

from gi.repository import Gio, GLib, GObject
from loguru import logger

from ..utils.thread_utils import run_in_background

STK_AGENT_PATH = "/io/furios/Telephony/StkAgent"
STK_SESSION_AGENT_PATH = "/io/furios/Telephony/StkAgent/session"
GOBACK_ERROR = "org.ofono.Error.SimToolkit.GoBack"
END_SESSION_ERROR = "org.ofono.Error.SimToolkit.EndSession"
BUSY_ERROR = "org.ofono.Error.SimToolkit.Busy"

SIM_MENU_MARKER = re.compile(r"^[>\s]+")


def clean_sim_text(text):
    """Normalize a text the SIM provided for display.

    SIM menus commonly carry HTML entities and leading angle brackets
    that mark a submenu, both of which are noise once the text sits in
    a list row or a header.
    """
    if not text:
        return ""
    return SIM_MENU_MARKER.sub("", html.unescape(text)).strip()


VOID_REPLY_METHODS = ("DisplayText", "PlayTone", "LoopTone",
                      "DisplayActionInformation", "DisplayAction")
STRING_REPLY_METHODS = ("RequestInput", "RequestDigits", "RequestKey",
                        "RequestDigit", "RequestQuickDigit")
BOOLEAN_REPLY_METHODS = ("RequestConfirmation", "ConfirmCallSetup",
                         "ConfirmLaunchBrowser", "ConfirmOpenChannel")

STK_AGENT_XML = """
<node>
  <interface name="org.ofono.SimToolkitAgent">
    <method name="RequestSelection">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="a(sy)" direction="in"/>
      <arg type="n" direction="in"/>
      <arg type="y" direction="out"/>
    </method>
    <method name="DisplayText">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="in"/>
    </method>
    <method name="RequestInput">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestDigits">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestKey">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestDigit">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestQuickDigit">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestConfirmation">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="ConfirmCallSetup">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="PlayTone">
      <arg type="s" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
    </method>
    <method name="LoopTone">
      <arg type="s" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
    </method>
    <method name="DisplayActionInformation">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
    </method>
    <method name="ConfirmLaunchBrowser">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="DisplayAction">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
    </method>
    <method name="ConfirmOpenChannel">
      <arg type="s" direction="in"/>
      <arg type="y" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="Cancel"/>
    <method name="Release"/>
  </interface>
</node>
"""


class StkManager(GObject.Object):
    """Bridge between ofono's SIM Toolkit and the UI.

    The SIM drives this relationship: ofono calls the exported agent
    and each call stays pending until the user acts. The pending
    invocation is answered exactly once, from reply(), an error reply,
    or a cancellation, and only one request exists at a time by
    protocol design.
    """

    __gsignals__ = {
        'menu-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'agent-request': (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        'request-cancelled': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, ofono):
        """Initialize the manager and follow the SimToolkit interface."""
        super().__init__()
        self.ofono = ofono
        self.stk_proxy = None
        self.main_menu = []
        self.main_menu_title = ""
        self._registration_ids = []
        self._registered_bus = None
        self._pending = None
        self._pending_kind = None
        self._stk_handler_id = None
        self._registered = False
        self._handled = False
        ofono.connect('modem-interface-appeared', self._on_interface_appeared)

    def _on_interface_appeared(self, _mgr, interface):
        """Attach to the toolkit when the modem exports it."""
        if interface == "org.ofono.SimToolkit":
            self._attach()

    def _attach(self):
        """Create the proxy, export the agent and register it."""
        bus = self.ofono.bus
        path = self.ofono.modem_path
        if not bus or not path:
            return
        if self.stk_proxy:
            self.stk_proxy.run_dispose()
            self.stk_proxy = None
        try:
            self.stk_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono", path, "org.ofono.SimToolkit", None)
        except Exception as e:
            logger.error(f"[StkManager] Proxy init failed: {e}")
            return
        self._stk_handler_id = self.stk_proxy.connect("g-signal", self._on_stk_signal)
        self._export_agent(bus)
        run_in_background(self._seed_and_register)

    def _export_agent(self, bus):
        """Export the agent object on both fixed paths, once per bus."""
        if self._registered_bus is bus:
            return
        node = Gio.DBusNodeInfo.new_for_xml(STK_AGENT_XML)
        for agent_path in (STK_AGENT_PATH, STK_SESSION_AGENT_PATH):
            try:
                reg_id = bus.register_object(agent_path, node.interfaces[0],
                                             self._on_agent_call, None, None)
                self._registration_ids.append(reg_id)
            except Exception as e:
                logger.error(f"[StkManager] Agent export failed for {agent_path}: {e}")
                return
        self._registered_bus = bus

    def _seed_and_register(self):
        """Read the menu and register the default agent; blocking, call from a worker."""
        try:
            res = self.stk_proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            props = res.unpack()[0]
            GLib.idle_add(self._apply_menu_props, props)
        except Exception as e:
            logger.warning(f"[StkManager] Toolkit property read failed: {e}")
        self._register_default_agent()

    def _register_default_agent(self):
        """Register the default agent; blocking, call from a worker.

        ofono releases the agent whenever a session ends, including
        when the user rejects a prompt, so the registration has to be
        renewed or no further SIM initiated request is ever delivered.
        """
        if not self.stk_proxy or self._registered:
            return
        try:
            self.stk_proxy.call_sync("RegisterAgent",
                                     GLib.Variant("(o)", (STK_AGENT_PATH,)),
                                     Gio.DBusCallFlags.NONE, -1, None)
            self._registered = True
            logger.info("[StkManager] Default agent registered")
        except Exception as e:
            logger.warning(f"[StkManager] Agent registration failed: {e}")

    def _apply_menu_props(self, props):
        """Apply main menu properties on the main loop."""
        if "MainMenu" in props:
            self.main_menu = [(clean_sim_text(label), icon) for label, icon in props["MainMenu"]]
        if "MainMenuTitle" in props:
            self.main_menu_title = clean_sim_text(props["MainMenuTitle"])
        self.emit('menu-changed')
        return False

    def _on_stk_signal(self, _proxy, _sender, signal, params):
        """Track menu changes published by the SIM."""
        if signal != "PropertyChanged":
            return
        name, value = params.unpack()
        self._apply_menu_props({name: value})

    def has_menu(self):
        """Return whether the SIM currently offers a main menu."""
        return bool(self.main_menu)

    def select_item(self, index):
        """Enter a main menu item; blocking, call from a worker.

        Returns (True, None) on success or (False, error text).
        """
        if not self.stk_proxy:
            return (False, "no proxy")
        try:
            self.stk_proxy.call_sync(
                "SelectItem",
                GLib.Variant("(yo)", (index, STK_SESSION_AGENT_PATH)),
                Gio.DBusCallFlags.NONE, GLib.MAXINT, None)
            return (True, None)
        except Exception as e:
            logger.warning(f"[StkManager] SelectItem failed: {e}")
            return (False, str(e))

    def _on_agent_call(self, _conn, _sender, path, _iface, method, params, invocation):
        """Handle an agent method call from ofono."""
        if method in ("Cancel", "Release"):
            invocation.return_value(None)
            self._cancel_pending()
            if method == "Release" and path == STK_AGENT_PATH:
                self._registered = False
                run_in_background(self._register_default_agent)
            return
        if self._pending is not None:
            invocation.return_dbus_error(BUSY_ERROR, "Another request is active")
            return

        args = params.unpack()
        payload = self._build_payload(method, args)
        self._pending = invocation
        self._pending_kind = method
        self._handled = False
        self.emit('agent-request', method, payload)
        if self._pending is invocation and not self._handled:
            logger.warning("[StkManager] No surface took the request, ending the session")
            self.end_session()

    def _build_payload(self, method, args):
        """Translate agent call arguments into a UI facing payload."""
        if method == "RequestSelection":
            return {"title": clean_sim_text(args[0]),
                    "items": [clean_sim_text(label) for label, _icon in args[2]],
                    "default": args[3]}
        if method == "DisplayText":
            return {"text": clean_sim_text(args[0]), "urgent": args[2]}
        if method in ("RequestInput", "RequestDigits"):
            return {"title": clean_sim_text(args[0]), "default": args[2], "min": args[3],
                    "max": args[4], "hidden": args[5],
                    "digits": method == "RequestDigits"}
        if method in ("RequestKey", "RequestDigit", "RequestQuickDigit"):
            return {"title": clean_sim_text(args[0]), "digits": method != "RequestKey",
                    "quick": method == "RequestQuickDigit"}
        if method in ("RequestConfirmation", "ConfirmCallSetup", "ConfirmOpenChannel"):
            return {"text": clean_sim_text(args[0])}
        if method == "ConfirmLaunchBrowser":
            return {"text": clean_sim_text(args[0]), "url": args[2]}
        if method in ("PlayTone", "LoopTone"):
            return {"tone": args[0], "text": clean_sim_text(args[1]),
                    "loop": method == "LoopTone"}
        return {"text": clean_sim_text(args[0])}

    def discard_pending(self, invocation):
        """Drop a pending request the UI could not present."""
        if self._pending is invocation:
            self._pending = None
            self._pending_kind = None

    def _take_pending(self):
        """Detach and return the pending invocation."""
        invocation, kind = self._pending, self._pending_kind
        self._pending = None
        self._pending_kind = None
        return invocation, kind

    def mark_handled(self):
        """Record that a surface took responsibility for the request."""
        self._handled = True

    def reply(self, value=None):
        """Answer the pending request with the user's response."""
        invocation, kind = self._take_pending()
        if invocation is None:
            return
        if kind in VOID_REPLY_METHODS:
            invocation.return_value(None)
        elif kind == "RequestSelection":
            invocation.return_value(GLib.Variant("(y)", (int(value),)))
        elif kind in STRING_REPLY_METHODS:
            invocation.return_value(GLib.Variant("(s)", (str(value),)))
        elif kind in BOOLEAN_REPLY_METHODS:
            invocation.return_value(GLib.Variant("(b)", (bool(value),)))
        else:
            invocation.return_value(None)

    def reply_error(self, error_name):
        """Answer the pending request with a toolkit error."""
        invocation, _kind = self._take_pending()
        if invocation is not None:
            invocation.return_dbus_error(error_name, "answered by user")

    def go_back(self):
        """Tell the SIM the user backed out of the current request."""
        self.reply_error(GOBACK_ERROR)

    def end_session(self):
        """Tell the SIM the user ended the toolkit session."""
        self.reply_error(END_SESSION_ERROR)

    def _cancel_pending(self):
        """Resolve the pending request after the SIM cancelled it."""
        invocation, kind = self._take_pending()
        if invocation is None:
            return
        if kind in VOID_REPLY_METHODS:
            invocation.return_value(None)
        else:
            invocation.return_dbus_error(END_SESSION_ERROR, "cancelled by the SIM")
        self.emit('request-cancelled')
