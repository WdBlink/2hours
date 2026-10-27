"""Distribution identity shared by packaging, diagnostics, and runtime storage."""
import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TWO_HOURS_ROOT = SKILL_ROOT.parent.parent
TWO_HOURS = SKILL_ROOT.parent.name == "vendor" and (TWO_HOURS_ROOT / "scripts/review_bridge.py").is_file()
APP_NAME = "Two Hours" if TWO_HOURS else "Blurt"
BUNDLE_ID = "dev.twohours.recorder" if TWO_HOURS else "dev.blurt.recorder"
EXECUTABLE = "twohours-recorder" if TWO_HOURS else "blurt-recorder"
RECORDER_HOME = Path.home() / ".2hours/recorder" if TWO_HOURS else Path(os.environ.get("BLURT_HOME", Path.home() / ".blurt"))
PROJECT_FOLDER = ".2hours" if TWO_HOURS else ".blurt"
ICON = TWO_HOURS_ROOT / "assets/brand/icon.png" if TWO_HOURS else SKILL_ROOT / "assets/icon.png"
APP_BUNDLE = RECORDER_HOME / f"{APP_NAME}.app"
INSTALLED_APP = Path.home() / "Applications" / f"{APP_NAME}.app"


def identity():
    return {"name": APP_NAME, "bundle_id": BUNDLE_ID, "app": str(INSTALLED_APP),
            "home": str(RECORDER_HOME), "icon": str(ICON), "bundled": TWO_HOURS, "permissions": "not_tested"}
