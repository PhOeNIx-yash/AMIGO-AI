import unittest
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from amigo.utils.settings_resolver import resolve_setting

# Original reference tables before migration
EXPECTED_TABLE = [
    ("Bluetooth",            "ms-settings:bluetooth"),
    ("Wi-Fi",                "ms-settings:network-wifi"),
    ("Network",              "ms-settings:network"),
    ("VPN",                  "ms-settings:network-vpn"),
    ("Display",              "ms-settings:display"),
    ("Night Light",          "ms-settings:nightlight"),
    ("Sound",                "ms-settings:sound"),
    ("Notifications",        "ms-settings:notifications"),
    ("Battery Saver",        "ms-settings:batterysaver"),
    ("Power",                "ms-settings:powersleep"),
    ("Personalization",      "ms-settings:personalization"),
    ("Wallpaper",            "ms-settings:personalization-background"),
    ("Lock Screen",          "ms-settings:lockscreen"),
    ("Taskbar",              "ms-settings:taskbar"),
    ("Start Menu",           "ms-settings:personalization-start"),
    ("Themes",               "ms-settings:themes"),
    ("Accessibility",        "ms-settings:easeofaccess"),
    ("Magnifier",            "ms-settings:easeofaccess-magnifier"),
    ("Narrator",             "ms-settings:easeofaccess-narrator"),
    ("Mouse",                "ms-settings:mousetouchpad"),
    ("Touchpad",             "ms-settings:devices-touchpad"),
    ("Keyboard",             "ms-settings:keyboard"),
    ("Pen",                  "ms-settings:pen"),
    ("Clipboard",            "ms-settings:clipboard"),
    ("Storage",              "ms-settings:storagesense"),
    ("Apps",                 "ms-settings:appsfeatures"),
    ("Default Apps",         "ms-settings:defaultapps"),
    ("Startup Apps",         "ms-settings:startupapps"),
    ("Updates",              "ms-settings:windowsupdate"),
    ("Security",             "ms-settings:windowsdefender"),
    ("Privacy",              "ms-settings:privacy"),
    ("Location",             "ms-settings:privacy-location"),
    ("Camera",               "ms-settings:privacy-webcam"),
    ("Microphone",           "ms-settings:privacy-microphone"),
    ("Date",                 "ms-settings:dateandtime"),
    ("Time",                 "ms-settings:dateandtime"),
    ("Region",               "ms-settings:regionlanguage"),
    ("Language",             "ms-settings:regionlanguage"),
    ("Accounts",             "ms-settings:accounts"),
    ("Sign-in",              "ms-settings:signinoptions"),
    ("About",                "ms-settings:about"),
    ("Recovery",             "ms-settings:recovery"),
    ("Troubleshoot",         "ms-settings:troubleshoot"),
    ("Game Mode",            "ms-settings:gaming-gamemode"),
]

EXPECTED_ALIASES = {
    "wifi":           ("Wi-Fi",       "ms-settings:network-wifi"),
    "wi-fi":          ("Wi-Fi",       "ms-settings:network-wifi"),
    "internet":       ("Network",     "ms-settings:network"),
    "sleep":          ("Power",       "ms-settings:powersleep"),
    "background":     ("Wallpaper",   "ms-settings:personalization-background"),
    "brightness":     ("Display",     "ms-settings:display"),
    "volume":         ("Sound",       "ms-settings:sound"),
    "battery":        ("Battery Saver","ms-settings:batterysaver"),
    "antivirus":      ("Security",    "ms-settings:windowsdefender"),
    "defender":       ("Security",    "ms-settings:windowsdefender"),
    "update":         ("Updates",     "ms-settings:windowsupdate"),
    "windows update": ("Updates",     "ms-settings:windowsupdate"),
    "game":           ("Game Mode",   "ms-settings:gaming-gamemode"),
    "gaming":         ("Game Mode",   "ms-settings:gaming-gamemode"),
    "night":          ("Night Light", "ms-settings:nightlight"),
    "notification":   ("Notifications","ms-settings:notifications"),
    "dark mode":      ("Personalization","ms-settings:personalization-color"),
    "color":          ("Personalization","ms-settings:personalization-color"),
    "pin":            ("Sign-in",     "ms-settings:signinoptions"),
    "password":       ("Sign-in",     "ms-settings:signinoptions"),
    "mic":            ("Microphone",  "ms-settings:privacy-microphone"),
    "webcam":         ("Camera",      "ms-settings:privacy-webcam"),
    "gps":            ("Location",    "ms-settings:privacy-location"),
    "startup":        ("Startup Apps","ms-settings:startupapps"),
    "storage":        ("Storage",     "ms-settings:storagesense"),
    "disk":           ("Storage",     "ms-settings:storagesense"),
}


class TestSettingsResolverMigration(unittest.TestCase):
    """
    Verifies that migrating settings and aliases from hardcoded python constants
    to settings_catalog.json preserves 100% fidelity on URI resolution.
    """

    def test_all_expected_aliases_resolve_identically(self):
        for alias, (expected_label, expected_uri) in EXPECTED_ALIASES.items():
            label, uri = resolve_setting(alias)
            self.assertEqual(
                uri,
                expected_uri,
                f"Alias '{alias}' resolved to URI '{uri}', expected '{expected_uri}'"
            )

    def test_all_expected_table_labels_resolve_identically(self):
        for expected_label, expected_uri in EXPECTED_TABLE:
            label, uri = resolve_setting(expected_label)
            self.assertEqual(
                uri,
                expected_uri,
                f"Setting '{expected_label}' resolved to URI '{uri}', expected '{expected_uri}'"
            )


if __name__ == "__main__":
    unittest.main()

