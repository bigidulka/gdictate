from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

KWIN_RULES_FILE = Path.home() / ".config" / "kwinrulesrc"
KWIN_RULE_DESCRIPTION = "gdictate-hide-chrome"


def ensure_kwin_rule() -> None:
    if os.name == "nt":
        return
    if not KWIN_RULES_FILE.exists() and not shutil.which("qdbus6"):
        return

    import configparser
    import uuid

    config = configparser.ConfigParser()
    config.read(KWIN_RULES_FILE)

    for section in config.sections():
        if section != "General" and config.get(section, "Description", fallback="") == KWIN_RULE_DESCRIPTION:
            return

    rule_id = str(uuid.uuid4())
    count = config.getint("General", "count", fallback=0) + 1
    rules = config.get("General", "rules", fallback="")
    rules = f"{rules},{rule_id}" if rules else rule_id

    if not config.has_section("General"):
        config.add_section("General")
    config.set("General", "count", str(count))
    config.set("General", "rules", rules)

    config.add_section(rule_id)
    config.set(rule_id, "Description", KWIN_RULE_DESCRIPTION)
    config.set(rule_id, "wmclass", "chrome-localhost__speech-proxy")
    config.set(rule_id, "wmclassmatch", "2")
    config.set(rule_id, "skiptaskbar", "true")
    config.set(rule_id, "skiptaskbarrule", "2")
    config.set(rule_id, "skippager", "true")
    config.set(rule_id, "skippagerrule", "2")
    config.set(rule_id, "skipswitcher", "true")
    config.set(rule_id, "skipswitcherrule", "2")
    config.set(rule_id, "minimize", "true")
    config.set(rule_id, "minimizerule", "2")

    with KWIN_RULES_FILE.open("w", encoding="utf-8") as f:
        config.write(f)

    subprocess.run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"], capture_output=True)
    print("[KWIN] Window rule installed — Chrome hidden from taskbar/Alt+Tab", flush=True)
