from configparser import ConfigParser
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "unit,startup,calendar",
    [
        ("devagent-watch.timer", "2min", "*-*-* *:0/2:00 UTC"),
        ("devagent-reconcile.timer", "10min", "*-*-* *:0/30:00 UTC"),
    ],
)
def test_maintainer_timer_uses_persistent_calendar_schedule(unit, startup, calendar):
    path = Path(__file__).parents[1] / "infra/devagent" / unit
    config = ConfigParser(interpolation=None)
    config.read_string(path.read_text())

    timer = config["Timer"]
    assert timer["OnBootSec"] == startup
    assert timer["OnCalendar"] == calendar
    assert timer.getboolean("Persistent")
    assert "OnUnitActiveSec" not in timer
    assert "OnUnitInactiveSec" not in timer
