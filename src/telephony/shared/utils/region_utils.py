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

import os
import locale

from gi.repository import Gio
from telephony.shared.utils.log_utils import logger

MCC_TO_REGION = {
    "244": "FI", "202": "GR", "204": "NL", "206": "BE", "208": "FR", "212": "MC", "213": "AD", "214": "ES",
    "216": "HU", "218": "BA", "219": "HR", "220": "RS", "222": "IT", "226": "RO", "228": "CH", "230": "CZ",
    "231": "SK", "232": "AT", "234": "GB", "235": "GB", "238": "DK", "240": "SE", "242": "NO", "246": "LT",
    "247": "LV", "248": "EE", "250": "RU", "255": "UA", "257": "BY", "259": "MD", "260": "PL", "262": "DE",
    "266": "GI", "268": "PT", "270": "LU", "272": "IE", "274": "IS", "276": "AL", "278": "MT", "280": "CY",
    "282": "GE", "283": "AM", "284": "BG", "286": "TR", "288": "FO", "290": "GL", "292": "SM", "293": "SI",
    "294": "MK", "295": "LI", "297": "ME",
    "302": "CA", "310": "US", "311": "US", "312": "US", "313": "US", "314": "US", "315": "US", "316": "US",
    "330": "PR", "334": "MX", "338": "JM", "340": "GP", "342": "BB", "344": "AG", "346": "KY", "348": "VG",
    "350": "BM", "352": "GD", "354": "MS", "356": "KN", "358": "LC", "360": "VC", "362": "CW", "363": "AW",
    "364": "BS", "365": "AI", "366": "DM", "368": "CU", "370": "DO", "372": "HT", "374": "TT", "376": "TC",
    "702": "BZ", "704": "GT", "706": "SV", "708": "HN", "710": "NI", "712": "CR", "714": "PA", "716": "PE",
    "722": "AR", "724": "BR", "730": "CL", "732": "CO", "734": "VE", "736": "BO", "738": "GY", "740": "EC",
    "744": "PY", "746": "SR", "748": "UY", "750": "FK",
    "502": "MY", "505": "AU", "510": "ID", "514": "TL", "515": "PH", "520": "TH", "525": "SG", "528": "BN",
    "530": "NZ", "534": "MP", "535": "GU", "536": "NR", "537": "PG", "539": "TO", "540": "SB", "541": "VU",
    "542": "FJ", "543": "WF", "544": "AS", "545": "KI", "546": "NC", "547": "PF", "548": "CK", "549": "WS",
    "550": "FM", "551": "MH", "552": "PW",
    "400": "AZ", "401": "KZ", "402": "BT", "404": "IN", "405": "IN", "410": "PK", "412": "AF", "413": "LK",
    "414": "MM", "415": "LB", "416": "JO", "417": "SY", "418": "IQ", "419": "KW", "420": "SA", "421": "YE",
    "422": "OM", "424": "AE", "425": "IL", "426": "BH", "427": "QA", "428": "MN", "429": "NP", "430": "AE",
    "431": "AE", "432": "IR", "434": "UZ", "436": "TJ", "437": "KG", "438": "TM", "440": "JP", "441": "JP",
    "450": "KR", "452": "VN", "454": "HK", "455": "MO", "456": "KH", "457": "LA", "460": "CN", "461": "CN",
    "466": "TW", "467": "KP", "470": "BD", "472": "MV",
    "602": "EG", "603": "DZ", "604": "MA", "605": "TN", "606": "LY", "607": "GM", "608": "SN", "609": "MR",
    "610": "ML", "611": "GN", "612": "CI", "613": "BF", "614": "NE", "615": "TG", "616": "BJ", "617": "MU",
    "618": "LR", "619": "SL", "620": "GH", "621": "NG", "622": "TD", "623": "CF", "624": "CM", "625": "CV",
    "626": "ST", "627": "GQ", "628": "GA", "629": "CG", "630": "CD", "631": "AO", "632": "GW", "633": "SC",
    "634": "SD", "635": "RW", "636": "ET", "637": "SO", "638": "DJ", "639": "KE", "640": "TZ", "641": "UG",
    "642": "BI", "643": "MZ", "645": "ZM", "646": "MG", "647": "RE", "648": "ZW", "649": "NA", "650": "MW",
    "651": "LS", "652": "BW", "653": "SZ", "654": "KM", "655": "ZA", "657": "ER", "658": "SH", "659": "SS"
}

CALLING_CODE_TO_REGION = {
    "1": "US", "7": "RU", "20": "EG", "27": "ZA", "30": "GR", "31": "NL", "32": "BE", "33": "FR", "34": "ES",
    "36": "HU", "39": "IT", "40": "RO", "41": "CH", "43": "AT", "44": "GB", "45": "DK", "46": "SE", "47": "NO",
    "48": "PL", "49": "DE", "51": "PE", "52": "MX", "53": "CU", "54": "AR", "55": "BR", "56": "CL", "57": "CO",
    "58": "VE", "60": "MY", "61": "AU", "62": "ID", "63": "PH", "64": "NZ", "65": "SG", "66": "TH", "81": "JP",
    "82": "KR", "84": "VN", "86": "CN", "90": "TR", "91": "IN", "92": "PK", "93": "AF", "94": "LK", "95": "MM",
    "98": "IR", "211": "SS", "212": "MA", "213": "DZ", "216": "TN", "218": "LY", "220": "GM", "221": "SN",
    "222": "MR", "223": "ML", "224": "GN", "225": "CI", "226": "BF", "227": "NE", "228": "TG", "229": "BJ",
    "230": "MU", "231": "LR", "232": "SL", "233": "GH", "234": "NG", "235": "TD", "236": "CF", "237": "CM",
    "238": "CV", "239": "ST", "240": "GQ", "241": "GA", "242": "CG", "243": "CD", "244": "AO", "245": "GW",
    "246": "IO", "248": "SC", "249": "SD", "250": "RW", "251": "ET", "252": "SO", "253": "DJ", "254": "KE",
    "255": "TZ", "256": "UG", "257": "BI", "258": "MZ", "260": "ZM", "261": "MG", "262": "RE", "263": "ZW",
    "264": "NA", "265": "MW", "266": "LS", "267": "BW", "268": "SZ", "269": "KM", "290": "SH", "291": "ER",
    "297": "AW", "298": "FO", "299": "GL", "350": "GI", "351": "PT", "352": "LU", "353": "IE", "354": "IS",
    "355": "AL", "356": "MT", "357": "CY", "358": "FI", "359": "BG", "370": "LT", "371": "LV", "372": "EE",
    "373": "MD", "374": "AM", "375": "BY", "376": "AD", "377": "MC", "378": "SM", "380": "UA", "381": "RS",
    "382": "ME", "383": "XK", "385": "HR", "386": "SI", "387": "BA", "389": "MK", "420": "CZ", "421": "SK",
    "423": "LI", "500": "FK", "501": "BZ", "502": "GT", "503": "SV", "504": "HN", "505": "NI", "506": "CR",
    "507": "PA", "508": "PM", "509": "HT", "590": "GP", "591": "BO", "592": "GY", "593": "EC", "594": "GF",
    "595": "PY", "596": "MQ", "597": "SR", "598": "UY", "599": "CW", "670": "TL", "672": "NF", "673": "BN",
    "674": "NR", "675": "PG", "676": "TO", "677": "SB", "678": "VU", "679": "FJ", "680": "PW", "681": "WF",
    "682": "CK", "683": "NU", "685": "WS", "686": "KI", "687": "NC", "688": "TV", "689": "PF", "690": "TK",
    "691": "FM", "692": "MH", "850": "KP", "852": "HK", "853": "MO", "855": "KH", "856": "LA", "880": "BD",
    "886": "TW", "960": "MV", "961": "LB", "962": "JO", "963": "SY", "964": "IQ", "965": "KW", "966": "SA",
    "967": "YE", "968": "OM", "970": "PS", "971": "AE", "972": "IL", "973": "BH", "974": "QA", "975": "BT",
    "976": "MN", "977": "NP", "992": "TJ", "993": "TM", "994": "AZ", "995": "GE", "996": "KG", "998": "UZ"
}


def detect_region():
    """
    Detect the region based on the modem MCC or system locale.
    Returns None if detection fails.
    """
    try:
        modem_path = None
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

        manager = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.ofono", "/", "org.ofono.Manager", None)

        result = manager.call_sync("GetModems", None, Gio.DBusCallFlags.NONE, -1, None)
        modems = result.unpack()[0]

        if modems:
            modem_path = modems[0][0]

        if modem_path:
            net_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                "org.ofono", modem_path, "org.ofono.NetworkRegistration", None)

            props = net_proxy.call_sync("GetProperties", None, Gio.DBusCallFlags.NONE, -1, None)
            data = props.unpack()[0]

            mcc = data.get("MobileCountryCode")
            if mcc and mcc in MCC_TO_REGION:
                region = MCC_TO_REGION[mcc]
                logger.info(f"[Init] Detected Region from SIM (MCC {mcc}): {region}")
                return region
    except Exception as e:
        logger.error(f"[Init] Ofono Region Detection Failed: {e}")

    try:
        loc = locale.getlocale()[0]
        if not loc:
            loc = os.environ.get('LANG') or os.environ.get('LC_ALL') or os.environ.get('LC_CTYPE')
        if loc and '_' in loc:
            region = loc.split('_')[1].split('.')[0].upper()
            logger.info(f"[Init] Detected Region from Locale: {region}")
            return region
    except Exception as e:
        logger.warning(f"[Init] Region detection warning: {e}")

    return None


_SYSTEM_REGION = None
_CUSTOM_REGION = None


def set_custom_region(region_code):
    """
    Set a custom region override (e.g. from user settings).
    Can accept a region code (e.g. "FI") or a calling code (e.g. "358", "+358").
    """
    global _CUSTOM_REGION
    if not region_code:
        _CUSTOM_REGION = None
        return

    cleaned = region_code.strip().replace("+", "")

    if cleaned.isdigit() and cleaned in CALLING_CODE_TO_REGION:
        mapped_region = CALLING_CODE_TO_REGION[cleaned]
        logger.info(f"[Utils] Mapped calling code {cleaned} to region {mapped_region}")
        _CUSTOM_REGION = mapped_region
    else:
        _CUSTOM_REGION = cleaned.upper()


def get_system_region():
    """
    Get the cached system region or detect it if not available.
    """
    global _SYSTEM_REGION

    if _CUSTOM_REGION:
        return _CUSTOM_REGION

    if _SYSTEM_REGION is None:
        val = detect_region()
        if val:
            _SYSTEM_REGION = val
        else:
            logger.warning("[Init] Region detection failed, defaulting to FI")
            _SYSTEM_REGION = "FI"

    return _SYSTEM_REGION
