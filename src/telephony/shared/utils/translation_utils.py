# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

import builtins
import gettext
import locale
from telephony.shared.utils.log_utils import logger

LOCALEDIR = "/usr/share/locale"
DOMAIN = "telephony"


def install_i18n():
    try:
        try:
            locale.setlocale(locale.LC_ALL, "")
            logger.info(f"Locale set to: {locale.getlocale()}")
        except locale.Error as e:
            logger.warning(f"Failed to set locale: {e}")

        localedir = LOCALEDIR

        logger.info(f"Binding text domain '{DOMAIN}' to '{localedir}'")
        gettext.bindtextdomain(DOMAIN, localedir)
        gettext.textdomain(DOMAIN)

        logger.info("Installing gettext")
        gettext.install(DOMAIN, localedir)

    except Exception as e:
        logger.error(f"Failed to install i18n: {e}")
        builtins._ = lambda x: x
