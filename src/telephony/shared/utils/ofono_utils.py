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


def is_hfp_modem(path, props=None):
    """Return whether an oFono modem is a Bluetooth HFP modem."""
    props = props or {}
    return props.get("Type") == "hfp" or path == "/hfp" or path.startswith("/hfp/")


def get_first_non_hfp_modem(modems):
    """Return the first non-HFP modem tuple from GetModems(), or None."""
    for path, props in modems:
        if not is_hfp_modem(path, props):
            return path, props
    return None
