"""
Windows Settings URI Resolver for Amigo.
Single source of truth for all ms-settings: URI mappings.
Dynamic keyword + alias matching - no hardcoding at call sites.
"""

import os
import re
import random

_SETTINGS_TABLE = [
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

# Extra spoken aliases that do not appear literally in label text
_ALIASES = {
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

# Build keyword map: label words + aliases (all lowercase)
_KEYWORD_MAP = dict(_ALIASES)
for _label, _uri in _SETTINGS_TABLE:
    for _word in re.split(r"[\s]+", _label.lower()):
        _word = _word.strip("&-/")
        if _word and _word not in _KEYWORD_MAP:
            _KEYWORD_MAP[_word] = (_label, _uri)


def resolve_setting(setting: str) -> tuple:
    """
    Resolve a natural-language settings string to (display_name, ms-settings URI).
    Dynamic: keyword map + alias table + substring fallback. Zero hardcoded if/elif.
    """
    s = setting.lower().strip()

    # Generic/random/empty -> pick a practical random top entry
    _generic = {"", "random", "settings", "setting", "windows setting",
                "a random windows setting", "windows settings", "any setting",
                "a random windows", "windows", "window"}
    if s in _generic or s.startswith("random"):
        label, uri = random.choice(_SETTINGS_TABLE[:15])
        return label, uri

    # Full-phrase alias check first (multi-word like "dark mode", "windows update")
    if s in _KEYWORD_MAP:
        return _KEYWORD_MAP[s]

    # Word-by-word longest-score match
    words = re.split(r"[\s\-/&]+", s)
    best_label, best_uri, best_score = "Windows", "ms-settings:", 0
    for word in words:
        word = word.strip()
        if not word:
            continue
        if word in _KEYWORD_MAP:
            label, uri = _KEYWORD_MAP[word]
            score = len(word)
            if score > best_score:
                best_label, best_uri, best_score = label, uri, score

    if best_score > 0:
        return best_label, best_uri

    # Word-bounded fallback on full phrase
    query_tokens = set(re.findall(r"\b[a-z0-9]+\b", s))
    for label, uri in _SETTINGS_TABLE:
        lbl = label.lower()
        lbl_tokens = re.findall(r"\b[a-z0-9]+\b", lbl)
        if not lbl_tokens:
            continue
        if len(lbl_tokens) > 1 and re.search(r"\b" + re.escape(lbl) + r"\b", s):
            return label, uri
        if any(w in query_tokens for w in lbl_tokens if len(w) >= 3):
            return label, uri

    # Alias word-bounded fallback
    for alias, (label, uri) in _ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", s):
            return label, uri

    return "Windows", "ms-settings:"


def open_setting(setting: str) -> str:
    """Resolve, launch, and return a spoken confirmation string."""
    label, uri = resolve_setting(setting)
    os.system(f"start {uri}")
    return f"Opening {label} settings."