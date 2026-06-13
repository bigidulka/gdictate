from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.3.5"
WS_PORT = int(os.environ.get("GDICTATE_WS_PORT", "9876"))
CONTROL_PORT = int(os.environ.get("GDICTATE_CONTROL_PORT", "9877"))

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.environ.get("GDICTATE_PROJECT_DIR", PACKAGE_DIR.parent)).resolve()
SPEECH_PROXY_HTML = PROJECT_DIR / "speech-proxy.html"

LINUX_CHROME_PATHS = [
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]

WINDOWS_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

APP_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "gdictate"
CERT_DIR = APP_CACHE_DIR / "certs"
CHROME_PROFILE = APP_CACHE_DIR / "chrome-profile"
if os.name == "nt":
    APP_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "gdictate"
    CERT_DIR = APP_CACHE_DIR / "certs"
    CHROME_PROFILE = APP_CACHE_DIR / "chrome-profile"

APP_CONFIG_DIR = Path.home() / ".config" / "gdictate"
if os.name == "nt":
    APP_CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "gdictate"
SETTINGS_FILE = APP_CONFIG_DIR / "settings.json"
