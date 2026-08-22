"""Focused cross-platform tests for the dashboard's Eastern-time formatter."""

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from app import _strftime_compatible, fmt_et


def test_fmt_et_preserves_unpadded_day_and_12_hour_display():
    value = datetime(2026, 1, 5, 13, 7, tzinfo=timezone.utc)

    assert fmt_et(value, "%a %b %-d, %-I:%M %p ET") == "Mon Jan 5, 8:07 AM ET"


def test_windows_compatible_path_never_passes_posix_dash_directives():
    class WindowsStrftime:
        def strftime(self, fmt):
            if "%-" in fmt:
                raise ValueError("Invalid format string")
            return datetime(2026, 1, 5, 8, 7).strftime(fmt)

    assert _strftime_compatible(
        WindowsStrftime(), "%a %b %-d, %-I:%M %p ET"
    ) == "Mon Jan 5, 8:07 AM ET"


def test_fmt_et_keeps_padded_fields_and_iso_semantics():
    value = datetime(2026, 1, 5, 5, 7, tzinfo=timezone.utc)

    assert fmt_et(value, "%Y-%m-%d %H:%M ET") == "2026-01-05 00:07 ET"