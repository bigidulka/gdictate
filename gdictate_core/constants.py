from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.3.0-dev"
WS_PORT = int(os.environ.get("GDICTATE_WS_PORT", "9876"))
CONTROL_PORT = int(os.environ.get("GDICTATE_CONTROL_PORT", "9877"))

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.environ.get("GDICTATE_PROJECT_DIR", PACKAGE_DIR.parent)).resolve()
CERT_DIR = PROJECT_DIR / ".certs"
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

CHROME_PROFILE = Path.home() / ".cache" / "gdictate-chrome"
if os.name == "nt":
    CHROME_PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "gdictate" / "chrome-profile"

APP_CONFIG_DIR = Path.home() / ".config" / "gdictate"
if os.name == "nt":
    APP_CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "gdictate"
SETTINGS_FILE = APP_CONFIG_DIR / "settings.json"
