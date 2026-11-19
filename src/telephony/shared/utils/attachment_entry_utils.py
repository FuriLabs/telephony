# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

def attachment_file(entry):
    """Take the file out of one stored attachment. A message holds either the path on its own or a record of the path with the type and name beside it, and both shapes sit in the same column, so everything that reads an attachment has to accept either."""
    if isinstance(entry, dict):
        return str(entry.get("path") or entry.get("filepath") or "")
    return str(entry or "")
