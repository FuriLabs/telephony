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

import gi


def get_gst():
    """Return the initialized Gst module, importing it on first use.

    The import is what maps the GStreamer libraries into the process,
    so backend audio asks for the module here instead of at module
    scope: a daemon that never plays anything never pays for it.
    Python caches the module, so repeated calls cost a dict lookup.
    """
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    if not Gst.is_initialized():
        Gst.init(None)
    return Gst
