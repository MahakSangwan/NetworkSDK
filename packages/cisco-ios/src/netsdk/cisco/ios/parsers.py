"""Turn IOS command output into models.

Parsing is done by ntc-templates (D27); this layer maps template records into
our models, enforces the fields a model can't do without and rejects what we
don't support. Raised errors carry no host; the device adds it.
"""

from datetime import timedelta
from typing import Any

from netsdk.cisco.ios.commands import SHOW_VERSION
from netsdk.cisco.ios.models import IOSFacts
from netsdk.core import NotSupportedError, ParseError
from netsdk.core.parsing import parse_textfsm

PLATFORM = "cisco_ios"
VENDOR = "Cisco"

_UPTIME_UNITS = {
    # IOS counts uptime from its own timer; a year is taken as 365 days.
    "uptime_years": timedelta(days=365),
    "uptime_weeks": timedelta(weeks=1),
    "uptime_days": timedelta(days=1),
    "uptime_hours": timedelta(hours=1),
    "uptime_minutes": timedelta(minutes=1),
}


def parse_show_version(output: str) -> IOSFacts:
    rows = parse_textfsm(PLATFORM, SHOW_VERSION, output)
    if len(rows) != 1:
        raise ParseError(f"expected one {SHOW_VERSION!r} record, got {len(rows)}")
    row = rows[0]

    models: list[str] = row["hardware"]
    serials: list[str] = row["serial"]
    if len(models) > 1 or len(serials) > 1:
        raise NotSupportedError(
            f"{SHOW_VERSION!r} lists {max(len(models), len(serials))} chassis; "
            "switch stacks are not supported"
        )
    required = {
        "hostname": row["hostname"],
        "version": row["version"],
        "model": models,
        "serial number": serials,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ParseError(f"{SHOW_VERSION!r} output has no {', '.join(missing)}")

    return IOSFacts(
        hostname=row["hostname"],
        vendor=VENDOR,
        model=models[0],
        serial_number=serials[0],
        os_version=row["version"],
        uptime=_uptime(row),
        software_image=row["software_image"] or None,
        running_image=row["running_image"] or None,
        config_register=row["config_register"] or None,
        reload_reason=row["reload_reason"] or None,
    )


def _uptime(row: dict[str, Any]) -> timedelta | None:
    if not row["uptime"]:
        return None
    return sum(
        (int(row[field] or 0) * unit for field, unit in _UPTIME_UNITS.items()),
        timedelta(),
    )
