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

"""Pure derivations over a call dict, shared by every role.

The dict shape is the one OfonoManager tracks and the daemon
broadcasts: path to properties, with 'state' and 'multiparty' among
them. One formula here keeps the daemon's line budget and the
windows' button logic from ever disagreeing about the same calls.
"""


def conference_paths(calls):
    """Return the paths that belong to the conference."""
    return [p for p, d in calls.items() if d.get('multiparty')]


def count_lines(calls):
    """Count occupied lines; the conference occupies one line as a whole."""
    lines = len([p for p, d in calls.items() if not d.get('multiparty')])
    if any(d.get('multiparty') for d in calls.values()):
        lines += 1
    return lines


def held_single_paths(calls):
    """Return held calls that stand alone, outside the conference."""
    return [p for p, d in calls.items() if d.get('state') == 'held' and not d.get('multiparty')]


def held_conference_paths(calls):
    """Return held calls that belong to the conference."""
    return [p for p, d in calls.items() if d.get('state') == 'held' and d.get('multiparty')]
