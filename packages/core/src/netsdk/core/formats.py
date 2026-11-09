"""Canonical value formats shared by every model (D8).

Device packages hand vendor spellings to these types and get one format out,
so consumers never compare `aabb.ccdd.eeff` with `AA-BB-CC-DD-EE-FF`.

    MAC address  lowercase, colon separated: "aa:bb:cc:dd:ee:ff"
    speed        integer kbit/s (NetBox's unit): 1 Gbit/s == 1_000_000
    duplex       "full" | "half" | "auto"
    MTU          integer bytes
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator

_MAC_HEX = re.compile(r"[0-9a-f]{12}")
_MAC_SEPARATORS = re.compile(r"[.:\-]")


def normalize_mac(value: str) -> str:
    """Return `value` as "aa:bb:cc:dd:ee:ff"; accepts any common notation."""
    digits = _MAC_SEPARATORS.sub("", value.strip().lower())
    if not _MAC_HEX.fullmatch(digits):
        raise ValueError(f"not a MAC address: {value!r}")
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


#: A MAC address, normalized on validation.
MacAddress = Annotated[str, AfterValidator(normalize_mac)]

Duplex = Literal["full", "half", "auto"]

KBPS_PER_UNIT = {"k": 1, "m": 1_000, "g": 1_000_000, "t": 1_000_000_000}
