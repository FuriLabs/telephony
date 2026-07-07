# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

def is_hfp_modem(path, props=None):
    props = props or {}
    return props.get("Type") == "hfp" or path == "/hfp" or path.startswith("/hfp/")


def get_first_non_hfp_modem(modems):
    for path, props in modems:
        if not is_hfp_modem(path, props):
            return path, props
    return None
