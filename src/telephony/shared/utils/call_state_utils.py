# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""Pure derivations over a call dict, shared by every role.

The dict shape is the one OfonoManager tracks and the daemon
broadcasts: path to properties, with 'state' and 'multiparty' among
them. One formula here keeps the daemon's line budget and the
windows' button logic from ever disagreeing about the same calls.
"""


def conference_paths(calls):
    return [p for p, d in calls.items() if d.get("multiparty")]


def count_lines(calls):
    lines = len([p for p, d in calls.items() if not d.get("multiparty")])
    if any(d.get("multiparty") for d in calls.values()):
        lines += 1
    return lines


def held_single_paths(calls):
    return [p for p, d in calls.items() if d.get("state") == "held" and not d.get("multiparty")]


def held_conference_paths(calls):
    return [p for p, d in calls.items() if d.get("state") == "held" and d.get("multiparty")]
