"""Turn IOS command output into models.

Parsing is done by ntc-templates (D27); this layer maps template records into
our models, enforces the fields a model can't do without and rejects what we
don't support. Raised errors carry no host; the device adds it.
"""

import logging
import re
from datetime import timedelta
from typing import Any

from netsdk.cisco.ios.commands import SHOW_INTERFACES, SHOW_VERSION
from netsdk.cisco.ios.models import IOSFacts, IOSInterface
from netsdk.core import Duplex, NotSupportedError, ParseError
from netsdk.core.formats import KBPS_PER_UNIT
from netsdk.core.parsing import parse_textfsm

log = logging.getLogger(__name__)

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


# -- show interfaces ---------------------------------------------------------

# "100Mb/s", "1000Mbps", "10Gb/s"; "a-1000Mb/s" when auto-negotiated.
_SPEED = re.compile(r"(?:a-)?(\d+)\s*([kmgt])b(?:/s|ps)", re.IGNORECASE)
# The `BW 100000 Kbit` figure.
_BANDWIDTH = re.compile(r"(\d+)\s*([kmgt])bit", re.IGNORECASE)
_DUPLEX: dict[str, Duplex] = {"full": "full", "half": "half", "auto": "auto"}
_NOT_NEGOTIATED = {"", "auto-speed", "auto speed", "auto"}
_ADMIN_DOWN = {"administratively down", "deleted"}


def parse_show_interfaces(output: str) -> list[IOSInterface]:
    rows = parse_textfsm(PLATFORM, SHOW_INTERFACES, output)
    interfaces = [_interface(row) for row in rows]
    names = [interface.name for interface in interfaces]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ParseError(f"{SHOW_INTERFACES!r} lists {', '.join(duplicates)} more than once")
    return interfaces


def _interface(row: dict[str, Any]) -> IOSInterface:
    name = row["interface"]
    return IOSInterface(
        name=name,
        description=row["description"] or None,
        enabled=row["link_status"] not in _ADMIN_DOWN,
        oper_up=row["protocol_status"].startswith("up"),
        mac_address=row["mac_address"] or None,
        mtu=int(row["mtu"]) if row["mtu"] else None,
        speed=_speed(name, row["speed"]),
        duplex=_duplex(name, row["duplex"]),
        link_status=row["link_status"],
        protocol_status=row["protocol_status"],
        hardware_type=row["hardware_type"] or None,
        media_type=row["media_type"] or None,
        burned_in_address=row["bia"] or None,
        bandwidth=_kbps(_BANDWIDTH, row["bandwidth"]),
    )


def _kbps(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.fullmatch(text.strip())
    if match is None:
        return None
    return int(match[1]) * KBPS_PER_UNIT[match[2].lower()]


def _speed(name: str, text: str) -> int | None:
    if text.strip().lower() in _NOT_NEGOTIATED:
        return None
    kbps = _kbps(_SPEED, text)
    if kbps is None:
        # Unknown spellings are logged, not fatal: speed is optional and a
        # new release shouldn't break the whole getter.
        log.warning("%s: unrecognised speed %r", name, text)
    return kbps


def _duplex(name: str, text: str) -> Duplex | None:
    if not text:
        return None
    # "Full-duplex", "Full Duplex", "Auto-duplex", "a-full".
    word = re.sub(r"^a-", "", text.strip().lower()).split("-")[0].split()[0]
    duplex = _DUPLEX.get(word)
    if duplex is None:
        log.warning("%s: unrecognised duplex %r", name, text)
    return duplex
